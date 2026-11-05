# AI-assisted question drafting

Hosts can ask a free AI service to draft quiz questions. The Super Admin chooses the service, keeps the keys, sets the limits and sees every request. AI only ever drafts: a host reviews the questions and adds the ones they want.

## For hosts

In the quiz editor choose **Draft with AI** (or the same button in the live "Add a question now" drawer, which drafts one question). The dialog has three short steps and a live sentence at the bottom that says exactly what you are asking for. Your last choices are remembered.

| Step | What you set |
| --- | --- |
| 1. What is it about? | **Topic** (up to 200 characters, with example topics to click) and optional **focus or context**, such as "based on chapter 3 of our handbook, skip anything about pricing" (up to 500 characters) |
| 2. Who is it for? | Age group (5 to 7, 8 to 10, 11 to 13, 14 to 17, adults, mixed), knowledge of the topic (beginner, intermediate, advanced), language |
| 3. What should it ask? | Question type, difficulty, how many, and under **Style options**: tone (friendly, playful, formal), quiz setting, and whether to include a one-line explanation |

When you open the dialog from a quiz, **Use this quiz as context** is on. The AI is shown the quiz title and up to 40 of its latest questions so it does not repeat them and can match their style.

On the review screen each draft is a card with the correct answers marked. Things to know:

- A draft the AI was not fully sure about carries a **Please check this one** note saying what to verify.
- **Replace with** on a card asks for a new version of that one question: easier, harder, a different fact, or simpler words.
- **Draft more like these** adds more cards without repeating the ones you already have.
- **What was sent to the AI** shows the exact brief. Nothing about players is ever included.
- Untick any you do not want and choose **Add**. Nothing is added until you do.

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

## AI pictures

A host can create **one illustration for one question**. In the quiz editor, under the question's picture, choose **Create a picture with AI**. It is offered for single choice, multiple answers, true or false, poll, typed answer, number, order, match, buzzer and slide questions. It is greyed out for **pin** and **jigsaw** questions, where the picture is the puzzle and has to be real.

1. The AI reads your question and suggests a scene, or tells you a real picture would be better (for example for flags, maps, landmarks, famous people or diagrams, which an AI can draw wrongly). You can edit the description or press **Suggest an idea** again.
2. Choose a style (flat illustration, friendly cartoon, watercolour, pencil sketch) and the audience's age, then **Create picture**.
3. The picture appears as a preview. **Try again** draws another (it counts toward the limit). **Use this picture** uploads it like any picture you add yourself, and it is backed up with the rest. Until then nothing is stored.

Safety: the description is checked like a topic and always wrapped in fixed rules (no text, logos, real or famous people, weapons or anything frightening, and the stricter word list for younger audiences). The picture must really be a PNG, JPEG or WebP. Only the description is sent to the service; no player information is.

### Covers, explanation pictures and picture options

The same **Create with AI** flow now appears in three more places:

- **Quiz cover.** In the editor, under the quiz description, add a **Cover picture** (upload one or **Create a cover with AI**). It is a wide 16:9 banner drawn from the quiz title and description, saved with the quiz, copied when you duplicate it, included in the export, and shown on the quiz's card in your library.
- **Explanation picture.** Next to the explanation field, **Create with AI** draws a picture for it (offered for the same question types as above).
- **Picture options.** Above the answers of a single choice, multiple answers, poll or order question, **Create pictures for the options with AI** opens a set of cards, one per answer (up to six, prefilled with the option text). All are drawn in one chosen style as square, plain-background pictures so they look like a set. Edit what each should show, **Redraw** any single one, untick the ones you do not want, and press **Use**. Only ticked pictures are added. Each drawn picture counts as one request against your daily limit.

For a set of picture options the per-minute limit now defaults to 6 (it was 3), so a set of four is not throttled.

For the Super Admin, the AI page has an **AI pictures** card. It is off by default. Pictures use the keys already saved for **Google Gemini** (default model `gemini-2.5-flash-image`) and **Cloudflare** (default `@cf/black-forest-labs/flux-1-schnell`), or a **custom** service that follows the OpenAI images API and returns base64. Groq and OpenRouter's free text models cannot draw. Choose which service draws first, load its list of picture models, and set the limits (defaults: 20 pictures per host per day, 3 per minute, 100 for everyone per day). The text fallback setting also applies. Free tiers may use prompts and pictures to improve their products, so check each provider's terms, including who owns the pictures.

Every picture request is audited as **Picture** (with what it was for: question, explanation, option or cover), and every idea request as **Picture idea**. An entry shows the description, style, the exact prompt sent (with the fixed rules), each service tried with its result and time, the picture's size and SHA-256 fingerprint, and whether the host used it. The picture itself is not copied into the log.

## Projector screen: background picture, music and long descriptions

These are settings of the **quiz**, found in the editor under the quiz description, in **Cover, background and music**:

- **Projector background.** Upload a picture (PNG, JPEG or WebP, up to 2 MB), or create one with **Create a background with AI**. The big screen scales it to cover the whole screen at any resolution and centres it, so the edges may be trimmed. Use a wide (16:9) picture with calm areas. A soft wash over it keeps the text readable.
- **You always see whether it saved.** At the bottom of the **Cover, background and music** dialog a line says **Saved**, **Saving…**, or exactly why the quiz could not be saved (for example a question that still needs finishing, or a picture link from a site that is not allowed). A failed save also shows a message on the page. Pictures can be linked only from Giphy, Tenor, Imgur, Wikimedia, Unsplash, Pexels, Pixabay and Flickr; the link box checks this and refuses other sites at once, and any other picture must be uploaded.
- **A lobby follows its quiz.** A game is a copy of the quiz made when you open the lobby. While the quiz has not started, changes you make to the quiz's title, lobby text, background picture, picture strength or music reach the open lobby on its own within a few seconds, on the big screen and on phones. Once the quiz starts, the game keeps its own copy, so editing the quiz mid-game changes nothing.
- **Words stay readable on any picture.** With a background set, the title, lobby text, join code and footer sit on soft panels (on the big screen and on phones), so they stay clear however busy or bright the picture is, whatever the picture strength.
- **Bigger QR code, and full screen.** The QR code on the waiting screen now runs the full height of the right-hand column. A small expand button on the QR card, and one on the quiz description, opens a full-screen view for the back of the room: the QR with the join code, or the title with the description scrolling by itself. Press **Q** or **D** instead of clicking. **Esc**, the close button, or a click outside goes back, and the view closes by itself when the quiz starts. The host console's QR code also has the picture magnifier.
- **Picture strength.** Once a background picture is set, choose how strongly it shows: **Strong** (a soft wash keeps the words easiest to read), **Medium** (the default), **Light** or **None** (the picture as it is). Phones get a slightly stronger wash than the big screen. Words get a soft halo so they stay readable over a vivid picture. A GIF background animates like any other.
- **The background fits any shape.** A wide (16:9) picture fills the screen. A square or portrait picture, or any shape that would lose a lot to cropping, is shown whole with a soft blurred copy of itself filling the sides, on the big screen and on phones (and it adjusts when a phone is turned). If you already set a cover, **Use this as the projector background too** copies it across in one click. The cover (shown on the quiz card in your library) and the projector background are two separate pictures.
- **Waiting-screen music.** Upload one file: **MP3, M4A, OGG or WAV, up to 5 MB**. It loops on the big screen while players join and fades out when the quiz starts. It follows the screen's sound switch and volume, and (like all browser sound) starts after one click on the projector's sound button. Only use music you have the right to play.
- **On players' phones** the same quiz settings carry over. While players wait, the phone shows the lobby description in a box that fits the screen and scrolls by itself when it is long, and plays the music (with its own **Music on / off** button; the choice is remembered on that phone). When the quiz starts, the description goes and the music fades out. The background picture stays behind the whole game, under a stronger wash so text stays easy to read. In the **Host** dialog, a switch **Waiting-screen music on phones** (on by default, shown only for quizzes that have music) lets you turn the phone music off when everyone is in the room with the big screen, so it plays from one place.
- **Long lobby descriptions** no longer push the join code and player wall off the screen. The description gets the room left beside the QR code and scrolls slowly up and down by itself when it is longer than that (it pauses at each end, and stays still for people who ask their computer to reduce motion). The title shows on one line when there is a description.

Limits: pictures are 2 MB at most everywhere (the Super Admin can change this). Large photos are shrunk in the browser first, and animated GIFs bigger than that are shrunk by the server and stay animated. Other audio and video you attach to questions keep the existing limit.

## Audit log

Every request is recorded, including blocked and rate-limited ones, plus key tests and every change to AI settings. Each entry has the time, user, address, service and model, the exact choices, topic and focus text, how many questions were asked for, returned and finally added by the host, tokens, time taken, and notes on anything discarded. Open an entry to see five tabs: **Overview**, **Prompt** (the exact brief that was sent, the fixed system prompt, prompt version, temperature and token limit, with copy buttons), **Reply** (the raw reply before the safety checks), **Drafts** (what was kept, marked "check", or discarded) and **Attempts** (each service tried, with its result, time and tokens). The context used is recorded too: the quiz title, how many existing questions were sent, and any "replace this draft" request. The fixed system prompt is stored once and shared by all entries. Filter by result, action, user and date, and export to CSV. Entries older than the retention period (default 180 days) are deleted automatically. Settings changes never include key values.

## Where the key is kept

API keys are encrypted with a master key taken from `QUICKZ_SECRET_KEY` if set, otherwise from a random file `data/ai.key` created on first use. Keep that file out of version control (the `data` folder already is) and out of backups you share. If the database is restored on a machine without the master key, the keys cannot be read and the Super Admin simply enters them again.

## For developers

- `quickz/aimage.py`: AI pictures (question kinds allowed, safety template, each service's call, idea step, limits, audit).
- `quickz/ai.py`: request checks, prompt, provider calls, reply checks, the whole flow (`generate`).
- `quickz/db.py`: tables `ai_providers` (with `image_model`), `ai_settings`, `ai_audit`, `ai_prompts` (the fixed system prompt, stored once).
- Prompt design: `ai.SYSTEM_PROMPT` never changes between requests (difficulty, level, age and tone rubrics, option-quality rules, a plan, write, check, rate process, safety rules). `ai.build_prompt` writes the per-request brief with one worked example per requested type. Change the wording by editing those and bumping `ai.PROMPT_VERSION`, which is recorded in every audit entry.
- `quickz/server.py`: `/api/ai/*` for hosts, `/api/admin/ai/*` for the Super Admin.
- `quickz/static/js/ai.js`: the editor dialog and the admin page.
- `tests/test_aimage.py`: pictures, with a fake picture service.
- `tests/test_ai.py`: the network call is replaced by a fake, so the tests need no key or internet.

## Viewing pictures large

On phones and in the host console, any picture can be opened in a viewer: the question's picture, the pictures on answers, the explanation's picture, and the pictures you upload in the editor.

- **Open it:** tap or click the picture, or the small magnifier in its corner. On an answer, only the magnifier opens it, so tapping the answer still picks it. The tiny pictures on ordering and matching cards are left alone, because they are dragged.
- **Zoom and move:** pinch, double-tap, mouse wheel, or the **+ / −** and **Fit** buttons (keys **+**, **-**, **0**); drag to move around a zoomed picture.
- **Full screen:** the **Full screen** button (where the browser allows it). **Esc** or the **X** closes it.
- **Not for guessing games:** a question set to blur or zoom its picture during play cannot be opened until the answer is revealed, so the viewer cannot give the answer away. The big projector screen does not have the viewer.
