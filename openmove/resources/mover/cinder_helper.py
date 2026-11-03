# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""OpenMove's helper for the Cinder host (docs/MOVER.md). Python 2 and 3 compatible, like the playbook script it follows.

It is sent over SSH and run with the interpreter that Cinder itself uses (the host registry stores it), as
    <cinder python> - <verb> <base64 of one JSON object>
with this file on standard input. It prints one JSON line: {"ok": true, ...} or {"ok": false, "error": "..."}.

It does the few things that Cinder's REST API cannot do, through Cinder's own object layer, the same way Cinder creates the temporary
destination of a standard migration (VolumeManager._migrate_volume_generic):
  show           read one volume, its snapshots and its attachments
  find-host      the host string of the pools of a back-end (for the destination record)
  record-create  the destination record of the staged method (status available, migration_status target:<source id>)
  record-delete  remove the destination record without releasing quota (rollback R2)
  freeze         the source record: status retyping or maintenance, migration_status migrating. One conditional update, or nothing
  unfreeze       put the source record back exactly as it was (rollback R2)
  sync-fields    copy the fields a tenant can still change (name, description, bootable, metadata) from the source to the destination
  conf           read named options from cinder.conf (no secret is ever returned)
Nothing here talks to the arrays, to Nova, or to anything but the Cinder database through Cinder's objects.
"""
from __future__ import print_function

import base64
import json
import sys

RELATION_SKIP = set(['snapshots', 'cluster', 'glance_metadata', 'volume_type', 'volume_attachment', 'consistencygroup', 'group'])
DEST_SET_SKIP = set(['host', 'cluster_name', 'availability_zone'])
TENANT_FIELDS = ('display_name', 'display_description', 'bootable')
SECRET = ('password', 'secret', 'token', 'connection', 'key', 'auth')


class HelperError(Exception):
    pass


def init_cinder():
    from oslo_config import cfg
    from cinder import objects, version
    from cinder.common import config  # noqa: registers the Cinder options
    cfg.CONF([], project='cinder', version=version.version_string())
    objects.register_all()


def ctx():
    from cinder import context
    return context.get_admin_context()


def volume_dict(v):
    out = {}
    for k in v.fields:
        if k in ('volume_attachment', 'snapshots', 'volume_type', 'glance_metadata', 'cluster', 'group', 'consistencygroup'):
            continue
        if v.obj_attr_is_set(k):
            val = getattr(v, k)
            out[k] = val if isinstance(val, (str, int, float, bool, type(None))) else str(val)
    out['metadata'] = dict(v.metadata or {})
    return out


def do_show(a):
    from cinder import objects
    c = ctx()
    v = objects.Volume.get_by_id(c, a['volume'])
    snaps = objects.SnapshotList.get_all_for_volume(c, v.id)
    atts = [{'id': x.id, 'instance_uuid': x.instance_uuid, 'attach_status': x.attach_status} for x in v.volume_attachment]
    return {'volume': volume_dict(v), 'name_id': v.name_id,
            'snapshots': [{'id': s.id, 'status': s.status, 'volume_size': s.volume_size, 'created_at': str(s.created_at), 'name': s.display_name}
                          for s in snaps],
            'attachments': atts}


def do_find_host(a):
    from cinder import objects
    c = ctx()
    backend = a['backend']
    hosts = set()
    for svc in objects.ServiceList.get_all_by_topic(c, 'cinder-volume', disabled=False):
        host = svc.host                                       # host@backend
        if host.split('@', 1)[-1] == backend:
            hosts.add(host)
    return {'hosts': sorted(hosts)}


def build_values(src, ref, dest_type, manager_skip):
    skip = set(DEST_SET_SKIP) | set(RELATION_SKIP) | set(manager_skip)
    values = {}
    for key in src.fields:
        if key in skip or not src.obj_attr_is_set(key):
            continue
        values[key] = getattr(src, key)
    values.update({'volume_type_id': dest_type, 'provider_id': None, 'provider_location': None, 'provider_auth': None, 'provider_geometry': None,
                   'service_uuid': ref.service_uuid, 'shared_targets': ref.shared_targets, 'previous_status': None})
    return values


FROZEN_STATUS = ('maintenance', 'retyping')


def source_problems(status, migration_status, encrypted, multiattach, group):
    """Why a source volume may not be the origin of a destination record. The source is frozen (status maintenance or retyping with
    migration_status migrating) by OpenMove's own freeze step before the record is made, so that state is the expected one."""
    problems = []
    frozen = migration_status == 'migrating' and status in FROZEN_STATUS
    if status not in ('available', 'in-use') and not frozen:
        problems.append('source status %s' % status)
    if migration_status not in (None, 'success', 'error') and not frozen:
        problems.append('source is mid-migration (%s)' % migration_status)
    if encrypted or multiattach:
        problems.append('encrypted or multiattach')
    if group:
        problems.append('in a group')
    return problems


def do_record_create(a):
    from cinder import objects
    from cinder.objects import fields
    from cinder.volume import manager as vol_manager
    c = ctx()
    src = objects.Volume.get_by_id(c, a['src'])
    problems = source_problems(src.status, src.migration_status, src.encryption_key_id, src.multiattach, src.group_id or src.consistencygroup_id)
    if problems:
        raise HelperError('REFUSED: ' + '; '.join(problems))
    # an earlier attempt that was interrupted leaves a record that points at this source: use it, never make a second
    for v in objects.VolumeList.get_all(c, filters={'migration_status': 'target:%s' % src.id}):
        return {'new': v.id, 'existed': True}
    dest_host, dest_type = a['dest_host'], a['dest_type']
    ref = None
    if a.get('reference_volume'):
        ref = objects.Volume.get_by_id(c, a['reference_volume'])
    else:
        for v in objects.VolumeList.get_all_by_host(c, dest_host) if hasattr(objects.VolumeList, 'get_all_by_host') else []:
            ref = v
            break
    if ref is None:
        raise HelperError('no reference volume on %s: give one (a volume that exists on the destination back-end)' % dest_host)
    if ref.host != dest_host and ref.host.split('#')[0] != dest_host.split('#')[0]:
        raise HelperError('the reference volume is not on the destination back-end')
    values = build_values(src, ref, dest_type, vol_manager.VolumeManager._VOLUME_CLONE_SKIP_PROPERTIES)
    from cinder.volume import volume_utils
    svc = objects.Service.get_by_args(c, volume_utils.extract_host(dest_host), 'cinder-volume')
    if a.get('dry_run'):
        return {'dry_run': True, 'fields': sorted(values), 'availability_zone': svc.availability_zone}
    new = objects.Volume(context=c, host=dest_host, cluster_name=None, availability_zone=svc.availability_zone, status='available',
                         attach_status=fields.VolumeAttachStatus.DETACHED, migration_status='target:%s' % src.id, **values)
    new.create()
    return {'new': new.id, 'existed': False, 'availability_zone': svc.availability_zone}


def do_record_delete(a):
    from cinder import exception, objects
    c = ctx()
    try:
        v = objects.Volume.get_by_id(c, a['new'])
    except exception.VolumeNotFound:
        return {'deleted': False, 'absent': True}
    if not (v.migration_status or '').startswith('target:'):
        raise HelperError('record %s is not a destination record (migration_status %s): it is not deleted' % (v.id, v.migration_status))
    v.destroy()                                               # marks the row deleted; quota was never reserved for it, so none is released
    return {'deleted': True}


def do_freeze(a):
    from cinder import objects
    c = ctx()
    v = objects.Volume.get_by_id(c, a['volume'])
    ns, ps = a['status'], a['previous_status']
    prior = {'status': v.status, 'previous_status': v.previous_status, 'migration_status': v.migration_status}
    if v.migration_status == 'migrating' and v.status == ns:
        return {'frozen': True, 'already': True, 'before': prior}
    ok = v.conditional_update({'status': ns, 'previous_status': ps, 'migration_status': 'migrating'},
                              {'status': ps, 'migration_status': [None, 'success', 'error']})
    if not ok:
        raise HelperError('the freeze changed nothing: the volume is %s/%s now' % (v.status, v.migration_status))
    return {'frozen': True, 'before': prior}


def do_unfreeze(a):
    from cinder import objects
    c = ctx()
    v = objects.Volume.get_by_id(c, a['volume'])
    if v.migration_status != 'migrating':
        return {'unfrozen': False, 'note': 'migration_status is %s: not frozen' % v.migration_status}
    ok = v.conditional_update({'status': a['status'], 'previous_status': a.get('previous_status'), 'migration_status': a.get('migration_status')},
                              {'migration_status': 'migrating'})
    return {'unfrozen': bool(ok)}


def do_sync_fields(a):
    from cinder import exception, objects
    c = ctx()
    try:
        new = objects.Volume.get_by_id(c, a['new'])
    except exception.VolumeNotFound:
        return {'changed': [], 'note': 'the destination record is gone: completion has run'}
    if not (new.migration_status or '').startswith('target:'):
        return {'changed': [], 'note': 'the destination left the target state (%s): completion is running' % new.migration_status}
    src = objects.Volume.get_by_id(c, a['src'])
    changed = []
    for key in TENANT_FIELDS:
        if getattr(new, key) != getattr(src, key):
            setattr(new, key, getattr(src, key))
            changed.append(key)
    if dict(new.metadata or {}) != dict(src.metadata or {}):
        new.metadata = dict(src.metadata or {})
        changed.append('metadata')
    if changed:
        new.save()
    return {'changed': changed}


def do_conf(a):
    from oslo_config import cfg
    out = {}
    conf = cfg.CONF
    for want in a.get('options', []):
        section, _, name = want.partition('.')
        if any(w in name.lower() for w in SECRET):
            out[want] = '[withheld]'
            continue
        try:
            out[want] = str(getattr(conf, name) if section in ('DEFAULT', '') else getattr(getattr(conf, section), name))
        except Exception:
            out[want] = None
    return {'options': out}



def _sanitise_conf(path):
    """cinder.conf with every secret value and every credential in a URL replaced. Sections and keys stay."""
    import re
    out = []
    try:
        for line in open(path).read().splitlines():
            m = re.match(r'^(\s*[\w.\-]+\s*=\s*)(.*)$', line)
            if m and any(w in m.group(1).lower() for w in SECRET + ('connection', 'transport_url', 'memcache')):
                out.append(m.group(1) + '[withheld]')
            else:
                out.append(re.sub(r'(://[^:/@\s]+):[^@\s]+@', r'\1:[withheld]@', line))
    except Exception as exc:
        out.append('# could not read %s: %s' % (path, exc))
    return '\n'.join(out)


def _run(cmd):
    import subprocess
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        o, e = p.communicate()
        return {'rc': p.returncode, 'out': o.decode('utf-8', 'replace')[-400000:], 'err': e.decode('utf-8', 'replace')[-2000:]}
    except Exception as exc:
        return {'error': str(exc)}


def do_diag(a):
    """Versions, the sanitised configuration, the services, the volumes of the run and the log lines that mention them. Read only."""
    import os
    res = {}
    try:
        import pkg_resources
        want = ('cinder', 'nova', 'oslo.db', 'oslo.messaging', 'oslo.config', 'sqlalchemy', 'oslo.versionedobjects')
        res['packages'] = sorted('%s==%s' % (d.project_name, d.version) for d in pkg_resources.working_set if d.project_name.lower() in want)
    except Exception as exc:
        res['packages_error'] = str(exc)
    res['python'] = sys.version
    res['conf'] = _sanitise_conf(a.get('conf_path') or '/etc/cinder/cinder.conf')
    from cinder import objects
    c = ctx()
    try:
        res['services'] = [{'host': s.host, 'binary': s.binary, 'disabled': s.disabled, 'up': s.is_up, 'zone': s.availability_zone, 'updated_at': str(s.updated_at)}
                           for s in objects.ServiceList.get_all(c)]
    except Exception as exc:
        res['services_error'] = str(exc)
    vols = {}
    ids = list(a.get('volumes', []))[:60] + list(a.get('records', []))[:60]
    for vid in ids:
        try:
            vols[vid] = do_show({'volume': vid})
        except Exception as exc:
            vols[vid] = {'error': str(exc)}
    res['volumes'] = vols
    lines = []
    paths = a.get('log_paths') or ['/var/log/cinder/cinder-volume.log', '/var/log/cinder/volume.log', '/var/log/cinder/cinder-api.log']
    limit = int(a.get('max_lines') or 600)
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, 'rb') as f:
                    data = f.read()[-4000000:].decode('utf-8', 'replace').splitlines()
                keep = [x for x in data if any(i in x for i in ids)] or data[-100:]
                lines.append({'path': p, 'lines': keep[-limit:]})
            except Exception as exc:
                lines.append({'path': p, 'error': str(exc)})
    jr = _run(['journalctl', '--no-pager', '-n', '6000', '-o', 'short-iso'])
    if jr.get('out'):
        keep = [x for x in jr['out'].splitlines() if any(i in x for i in ids) or ' ERROR ' in x]
        lines.append({'path': 'journalctl', 'lines': keep[-limit:]})
    res['log_lines'] = lines
    return res


VERBS = {'show': do_show, 'find-host': do_find_host, 'record-create': do_record_create, 'record-delete': do_record_delete,
         'freeze': do_freeze, 'unfreeze': do_unfreeze, 'sync-fields': do_sync_fields, 'conf': do_conf, 'diag': do_diag}


def main(argv):
    try:
        if len(argv) < 2 or argv[1] not in VERBS:
            raise HelperError('unknown verb')
        args = json.loads(base64.b64decode(argv[2]).decode('utf-8')) if len(argv) > 2 else {}
        init_cinder()
        res = VERBS[argv[1]](args)
        res['ok'] = True
    except HelperError as exc:
        res = {'ok': False, 'error': str(exc)}
    except Exception as exc:
        res = {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
    sys.stdout.write(json.dumps(res) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
