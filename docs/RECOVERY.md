# Recovery: if OpenMove, the computer or the network stops in the middle of a move

A move can take hours or days, and the computer it runs on can sleep, restart or lose its network. The rule of this tool is that **a stop costs nothing and needs no clean-up**: when OpenMove is running again, the move carries on from where it was, and the person at the screen has to do as little as possible.

This page says what protects the move, what a stop looks like on screen, and what is not covered.

## What protects the move

| Layer | What it does | Where it is |
|---|---|---|
| **The journal** | Every step of every volume is written to the journal before the change is sent and again when the answer is read. A stop between "sent" and "answered" leaves a step without a result. The engine finds it at the next look, reads the volume in the cloud, and closes the step with what the cloud shows. It never sends a retype twice. | `journal.db`, `engine/runner.py` (`settle`) |
| **The saved work** | The pre-flight run, the choice of volumes, the options, the releases, and the cloud address, user and project are saved to `resume.json` every few seconds and after every change. The file never holds a password or a cloud token. Approvals are kept in the journal, per pre-flight run, so a restored run keeps the approvals it had. | `recovery.py` |
| **The access address** | The address token is kept in `access-token` in the data folder, so the page that is open, and the address printed earlier, still work after a restart. | `recovery.token` |
| **The supervisor** | `--supervise` runs OpenMove as a child process. If it ends by itself or stops answering for about 80 seconds, the supervisor starts it again within seconds. `OpenMove-Lab.bat` and `OpenMove.bat` use it. | `supervisor.py` |
| **The retrying loop** | A move that meets an error does not stop. The loop waits (longer each time, up to a minute) and looks again, because each look reads the cloud afresh. Only 40 errors in a row hand the volumes back to a person (the next start tries again). | `Engine._loop` |
| **The guardian** | A thread beside the server. It saves the context, starts the engine's thread again if it ever died, and, on Windows, asks the computer not to go to sleep by itself while a move is running. | `recovery.Guardian` |
| **Sleep and stall notice** | A silence of more than 90 seconds between two looks is written to the journal (`engine_gap`) and shown on the Progress page. Every volume is read afresh, so the pause costs time only. | `Engine._loop` |
| **One program per data folder** | A second OpenMove on the same data folder is refused (`openmove.lock`), and on Windows two programs cannot share a port. Two programs on one journal would each move the same volumes. The lock is released by the system when the process ends, however it ends. | `recovery.acquire_lock` |
| **The permit** | Every change, restored or not, asks the same permit as always: release validated, gate approved, no halt. A restart can never do something the people had not already allowed. | `safety.py` |

## What happens after a stop

1. **The program ends** (crash, closed window, `taskkill`, power cut, restart of the computer). The journal and `resume.json` are on disk. A normal close writes `server_stop` in the journal; anything else leaves the next start to find that the last run was cut off.
2. **The supervisor starts it again** (if it was started with `--supervise` and only the OpenMove process ended). If the whole computer restarted, start `OpenMove-Lab.bat` (or `OpenMove.bat`) again.
3. **The page shows what is going on.** While OpenMove is away, the page shows a bar at the top ("OpenMove is not answering ... Your work is saved") and tries again every two seconds. When OpenMove answers, the page is brought up to date by itself.
4. **OpenMove puts the work back.** The lab signs in by itself, and restores at once. A customer cloud needs the password again (it is never stored): the page shows "OpenMove was restarted. Your work is saved." with a **Sign in again** button that fills in the address, user and project. When the sign-in works, OpenMove reads the platform, the volumes and the audit again, puts the saved pre-flight, choice, options and releases back, checks the migration host again if class C volumes are in flight, and **carries on the move that was running** (journal event `engine_resume`). The page says "Back where you were. The move carried on by itself with N volumes."
5. **Nothing was repeated.** Steps that were finished stay finished. Steps that were cut off are read back from the cloud first.

If something cannot be put back, the page says what and gives **Try again** and **Start fresh**. Start fresh forgets the saved work (it asks for a name); the history of every step already taken stays.

## When the move is waiting for a person

A move that "does not progress" is very often waiting for a second person: the copy is finished and checked, and G2 and G3 (before the switch) or G4 (after it) need the signature of a person who is not the operator. The Progress page now says so in a sentence at the top ("Nothing is stuck. 7 volumes are copied and checked, and waiting for a second person to approve ..."). A retry or a pause of the computer is shown the same way.

## What a move does when the computer sleeps

On Windows OpenMove asks the system not to go to sleep by itself while a move is running (`SetThreadExecutionState`; the screen may still turn off). This does not stop a sleep you ask for (the lid, the power button, Start > Sleep) or a policy that forces it. If the computer sleeps anyway, the loop sees the silence when it wakes, records it, and reads every volume afresh. On other systems, keep the machine awake with the usual means (for example `systemd-inhibit` or `caffeinate`) while a move runs.

A sleeping computer cannot move volumes. Class C copies run on the migration host under `nohup`, so a copy that is already running there carries on if the migration host stays up, and OpenMove picks the answer up when it wakes.

## What is not covered

- **A restart of the whole computer does not start OpenMove again.** The supervisor lives and dies with the computer. Put `OpenMove-Lab.bat` or `OpenMove.bat` in the Startup folder or a scheduled task at log-on if you want that (a decision for the site, not made here).
- **The lab's WSL distros stop with Windows.** `OpenMove-Lab.bat` starts them again (`lab.py up`); the lab's volumes and guests keep their state on disk.
- **A customer cloud needs the password again** after a restart. By design nothing that signs in is stored.
- **The supervisor cannot tell a hung move from a slow one.** It only asks whether the program answers (`GET /healthz`). A move that is waiting for the cloud is shown by the engine's own notes and the time limits (a retype that runs more than six hours halts and asks a person).
- **A damaged data folder** (disk failure) is not repaired. Copy `journal.db`, `resume.json` and `evidence/` with the usual backups.

## Settings and constants

| Value | Where | Meaning |
|---|---|---|
| `--supervise` | command line | Run under the supervisor. The batch files add it. |
| 5 s | `LOOP_PAUSE` | Pause between two looks of the engine. |
| 90 s | `GAP_SECONDS` | A longer silence is recorded as a sleep or a stall. |
| 40 | `MAX_RETRIES` | Errors in a row (with a pause that grows to 60 s) before the loop gives the volumes back to a person. |
| 15 s | `BEAT_EVERY` | Longest time between two saves of `resume.json`. |
| 10 s | `GUARD_EVERY` | How often the guardian looks. |
| 20 s x 4 | `HEALTH_EVERY`, `HEALTH_FAILS` | The supervisor asks every 20 s and restarts the program after 4 failed looks. |
| 8 within 120 s | `MAX_RAPID`, `RAPID_SECONDS` | The supervisor gives up when the program stops 8 times in a row, each within 2 minutes of its start, and says so. |
| 14 days | `MAX_AGE_HOURS` | Saved work older than this is not put back. |

## Where it is tested

`tests/test_recovery.py`: the loop retries, notices a sleep and ends after too many errors; a dead thread is started again; a cut-off run is carried on and a person's stop is not; the saved file holds no password; a cut-off program waits for the sign-in and puts everything back; another project is refused; Start fresh keeps the history; the supervisor restarts a crash, does not restart a normal end or a refused setting, gives up after quick stops, and restarts a program that does not answer; a second program on the same data folder is refused.

It was also tried on the lab: OpenMove was killed with `Stop-Process -Force` five times while volumes were in flight, once just after a retype was sent. After the last three kills the supervisor started it again within a second or two, the lab signed in by itself, the saved work was put back, all ten volumes were taken up again with their approvals, and the journal had no unresolved step. (The first kills showed two faults, both fixed: two programs could run on one data folder, and the class C volumes were not taken up again until the migration host was checked again.)
