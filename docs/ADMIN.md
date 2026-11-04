# Super Admin: Settings and clean-up

Open **Settings** in the top bar (Super Admins only). It has three parts.

## Limits, uploads and security

These already existed as fixed numbers or server environment variables. Now they can be changed while the server runs. Each change is checked against a safe range, applies at once, is kept when the server restarts, and is recorded (who, what, from and to).

| Setting | Default | Allowed | What it does |
| --- | --- | --- | --- |
| Players per game | 500 (or `QUICKZ_MAX_PLAYERS`) | 10 to 5000 | The most players who can join one game |
| Questions per quiz | 200 | 10 to 500 | Quizzes over this cannot be saved. Existing quizzes are not changed |
| Largest picture | 2 MB | 1 to 5 MB | Photos are shrunk in the browser first, and big animated GIFs by the server |
| Largest animated GIF to accept | 25 MB | 3 to 50 MB | A GIF bigger than the largest picture is shrunk automatically to fit it (see below). GIFs bigger than this are refused |
| Largest lobby music file | 5 MB | 1 to 20 MB | MP3, M4A, OGG or WAV only |
| Largest question audio or video | 50 MB (or `QUICKZ_MAX_MEDIA_MB`) | 1 to 500 MB | Clips attached to questions |
| Stay signed in for | 7 days | 1 to 90 days | Applies to new sign-ins |
| Shortest password | 10 | 8 to 64 | Applies when a password is next set. Existing passwords still work |
| Wrong passwords before a lock | 5 | 3 to 20 | Per username |
| Sign-in pause after a lock | 5 minutes | 1 to 120 minutes | |

A changed setting shows a **Changed** tag and a **Reset** link that goes back to the value the server started with (the default). **Reset all to defaults** does this for every setting. Setting a value equal to the default simply stops storing it.

## Animated GIFs

A GIF that fits the picture limit is stored exactly as uploaded. A bigger one (up to the GIF limit above) is shrunk by the server and stays animated: first a smaller picture and fewer colours, then fewer frames if it is still too big (the time of dropped frames is added to the rest, so the speed is the same). The host sees how much it was shrunk. Transparent areas become white. A GIF that cannot be brought under the limit, or is extremely large or long, is refused with a clear message and nothing is stored. This needs the Pillow library, which is in `requirements-prod.txt`; without it big GIFs are refused rather than shrunk.

## Clean up quizzes

Lists every host's quizzes in one table: title, host, questions, how often and when last played, when last edited, and size. Filter by title, host, "never played", "not played for 30 days / 90 days / 6 months / a year" and "not edited for ...", and sort by oldest edit, longest unplayed, most played or largest. Tick quizzes (or tick the box in the header for everything shown) and choose **Delete selected**.

- You are asked to confirm, with the titles listed and the consequences.
- **Games already played keep their results.** A game is played from its own copy of the quiz.
- An **event** that used a deleted quiz is kept but left without a quiz. Quizzes used by events are tagged in the list.
- Up to 500 quizzes can be deleted in one go. Deleting cannot be undone, so use the **Export** option on a quiz first if you might want it back.

## Unused uploaded files

Deleting quizzes leaves their pictures and music behind. This finds files that no quiz, game or event uses and removes them. Files uploaded in the last 24 hours are never touched, because someone may still be editing a quiz they have not saved. If you use the backup bucket, copies there are not removed.

The page also shows the totals: quizzes, accounts, games, players, answers, database size and uploaded files, and a **Recent changes** list. Every settings change and every clean-up is recorded in the audit log on the **AI** page, as **Admin settings** and **Cleanup** entries.

## For developers

- `quickz/adminconf.py`: the list of settings with their ranges, how they are applied, and the unused-file scan.
- `quickz/db.py`: table `app_settings`, and `quiz_overview`, `delete_quizzes`, `storage_counts`.
- `quickz/server.py`: `/api/admin/settings` (GET, PUT), `/api/admin/settings/reset`, `/api/admin/storage`, `/api/admin/quizzes` and `.../delete`, `/api/admin/uploads/cleanup`.
- `quickz/static/js/admin.js`: the page. `tests/test_admin_settings.py`: access control, ranges, effects, restart, bulk delete and file clean-up.
