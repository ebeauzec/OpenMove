# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Array adapters (docs/MOVER.md): how the staged method reaches the storage behind a Cinder back-end.

An adapter turns the few things the method needs into calls on the data mover (connectors/ssh.py, resources/mover/mover.py): find the
device of a volume or of one of its snapshots, make a staging volume on the target, take a snapshot of it under the name the target
driver expects, give it the name the driver derives for the destination record, copy and compare, and remove what the run created.

Which adapters exist is a matter of fact and is shown, never assumed:
  lvm         built and proven in the OpenStack lab (two LVM back-ends stand in for the two arrays)
  solidfire   NOT built. It needs a SolidFire cluster (Element API) to write and to prove against.
  pure        NOT built. It needs a FlashArray (REST API) to write and to prove against.
Without an adapter a back-end family is recognised (backends.py) but its class C volumes are held back with the cause "no adapter".
"""
from .base import AdapterError, UnsupportedFamily, adapter_for, families  # noqa: F401
