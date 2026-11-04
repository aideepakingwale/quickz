# AI-assisted question drafting

Hosts can ask a free AI service to draft quiz questions. The Super Admin chooses the service, keeps the keys, sets the limits and sees every request. AI only ever drafts: a host reviews the questions and adds the ones they want.

## For hosts

In the quiz editor choose **Draft with AI**. Fill in:

| Field | Choices |
| --- | --- |
| Topic | Free text, up to 200 characters. Describe the subject in words. |
| Quiz type | General knowledge, classroom or revision, trivia night, team building, workplace training, family and friends, community event |
| Question type | A mix, single choice, true or false, several answers, typed answer, number, order, match pairs |
| Difficulty | Easy, medium, hard |
| Audience knowledge level | Beginner, intermediate, advanced |
| Audience age group | 5 to 7, 8 to 10, 11 to 13, 14 to 17, adults, mixed ages |
| How many | 1 up to the admin's limit (default 10) |
| Language | English and nine others |

The options are built to suit the question type you pick, and the server checks that what comes back really is that type:

| Question type | What the AI must produce | Checked and fixed by the server |
| --- | --- | --- |
| Single choice | 4 different options, one correct | Fewer than 3 different options or no single key is discarded; extra options are trimmed (the answer is kept); the order is shuffled so the answer is not always in the same place |
| True or false | A statement and whether it is true | The options are always created as True and False |
| Several answers | 5 options, 2 or 3 correct, never all | Needs 4 or more options, 2 or more correct and not all; shuffled |
| Typed answer | The answer and alternative spellings | Duplicates removed (ignoring case) |
| Number | The number, an allowed error and a unit | Validated like a hand-written question |
| Put in order | 4 or 5 items in the correct order | 3 to 6 different items; phones see them shuffled |
| Match pairs | 4 pairs | 3 to 5 pairs with no repeated item on either side |
| Poll | 3 to 5 options and no right answer | Scores nothing; never part of "a mix" |

Anything of the wrong type, or that breaks these rules, is discarded and counted in the audit log. The drafts appear with the correct answers marked. Untick any you do not want and choose **Add**. They then behave like any hand-written question, so edit them freely. Always check facts before a live game.

## For the Super Admin

Open **AI** in the top bar.

1. Paste a free API key for one or more services and **Save**. Keys are encrypted on the server and never shown again (only the last four characters).
2. **Test connection** to confirm the key works. The test is recorded in the audit log.
3. Choose **Use this service** to pick the one that is used first. With *try the other services if the chosen one fails* on, the rest are tried in fallback order.
4. Switch **AI drafting** on. It is off until you do.

| Service | Where to get a free key | Notes |
| --- | --- | --- |
| Google Gemini | aistudio.google.com/apikey | Best quality of the free choices. Free-tier prompts may be used by Google to improve its products. |
| Groq | console.groq.com/keys | Fast. Open models only. |
| OpenRouter | openrouter.ai/keys | Free models are added and removed often. Keep as a backup. |
| Cloudflare Workers AI | dash.cloudflare.com/profile/api-tokens | Needs the account ID. Small daily allowance. |
| Other | any OpenAI-compatible https address | Private and local network addresses are refused. |

Free allowances and model names change often. Check the provider's own pages if requests start failing, and edit the model name in the panel.

## Guardrails

Always on:

- AI only drafts; nothing is added without a host choosing it.
- Only the form fields reach the AI. No player names, emails or answers are sent. The topic is placed inside a marked block and the AI is told to treat it as data.
- Web addresses, email addresses and instruction-like text in the topic are refused. A built-in list of blocked phrases applies to the topic and to what the AI writes, with a stricter list for under-14 audiences.
- The reply must be valid JSON and every question goes through the same validation as a hand-written one. Questions with links, blocked words, duplicates or broken answer keys are discarded.
- Choices (difficulty, age group and so on) come from fixed lists, never free text.
- Requests are made by the server, redirects are not followed, keys are sent only to the provider's own address, and keys are never logged.

Set by the Super Admin: master switch, requests per host per day and per minute, requests for everyone per day, most questions per request, wait time, extra blocked words, how long the audit log is kept.

## Audit log

Every request is recorded, including blocked and rate-limited ones, plus key tests and every change to AI settings. Each entry has the time, user, address, service and model, the exact choices and topic, how many questions were asked for, returned and finally added by the host, tokens, time taken, and notes on anything discarded. Open an entry to see the raw reply from the service before the safety checks. Filter by result, action, user and date, and export to CSV. Entries older than the retention period (default 180 days) are deleted automatically. Settings changes never include key values.

## Where the key is kept

API keys are encrypted with a master key taken from `QUICKZ_SECRET_KEY` if set, otherwise from a random file `data/ai.key` created on first use. Keep that file out of version control (the `data` folder already is) and out of backups you share. If the database is restored on a machine without the master key, the keys cannot be read and the Super Admin simply enters them again.

## For developers

- `quickz/ai.py`: request checks, prompt, provider calls, reply checks, the whole flow (`generate`).
- `quickz/db.py`: tables `ai_providers`, `ai_settings`, `ai_audit`.
- `quickz/server.py`: `/api/ai/*` for hosts, `/api/admin/ai/*` for the Super Admin.
- `quickz/static/js/ai.js`: the editor dialog and the admin page.
- `tests/test_ai.py`: the network call is replaced by a fake, so the tests need no key or internet.
