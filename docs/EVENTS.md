# Events: publish the QR code a week ahead

An **event** is a registration page that exists long before the quiz does. You publish its link or QR code
days or weeks ahead; people register once; on the day the same page lets them straight into the game.
Nothing is emailed by DiQuizBuzz (there is no mail service). Reminders come from the person's own calendar.

## What people see

1. **Before the quiz** - `https://<your-address>/e/spring-quiz-night-k7f2`. They enter their **full real name**, **email**,
   a **4-digit PIN**, pick an **avatar**, and tick the consent box. They then get "See you there!" with a countdown,
   **Add to Google Calendar** and an **Apple / Outlook (.ics)** file. Both carry reminders (a day before and 15 minutes before)
   and the link.
2. **On the day** - they open the same link (or scan the same QR). If they keep the page open, it takes them into the game
   by itself the moment you open the lobby: no form, with their registered name and avatar. (Phones poll gently, every 7-12
   seconds, so it can take a few seconds.)
3. **A different phone** - *Already registered? Sign in* with email + PIN.
4. **Walk-ins** - anyone can register on the spot **until you press Start**. After that the page says registration is closed;
   people who registered earlier can still get in.

## What you do (host console > Events)

1. **New event**: title, description, start time, length, capacity (default 1000), and the quiz.
2. **Share this**: copy the link, download the QR (SVG) or **Print poster**. The link and QR never change, so print them early.
3. **Open the lobby** when you are ready (this creates the live game, with real names and email switched on). Run it as usual
   from the live console.
4. Watch **Registered / here** fill in. **Copy emails** puts every address on your clipboard so you can send a reminder from
   your own mail app (or post the link in a WhatsApp group); **CSV** downloads the list.
5. A forgotten PIN: press **PIN** next to the person, then read them the new one.
6. **Delete** the event afterwards to erase every registration (names and emails). Games keep their own results.

## Rules worth knowing

- One registration per email. The email is *not verified* (nothing is sent), so the PIN is what stops someone else signing in
  as you. Wrong PINs are throttled: 5 tries, then 15 minutes' wait, per person and per address.
- Two people with the same name but different emails are both let in (the second shows as "John Smith (2)").
- A player you remove from the game cannot walk back in through the event page.
- Registration closes by itself at capacity, when you untick *Registration open*, or when the quiz starts.
- Times are shown in the event's time zone (the one your browser was in when you created it) and in each visitor's own.
  If you change the time after people added it to their calendar, their calendar will not update by itself: tell them.

## Hosting notes

- **The server must be running for the whole registration week.** Registrations, the page and the PINs live in the same database
  as everything else (and are included in the S3 backups). On AWS that means leaving the instance on (about $10.40/month for a
  t4g.micro), not the stop-between-events routine.
- **Do not delete or recreate the CloudFront stack once the QR code is printed.** The `dxxxx.cloudfront.net` address would change
  and every printed code would stop working. Updating the stack is fine.
- Capacity: the game itself allows `QUICKZ_MAX_PLAYERS` players (1000 in the AWS setup).

## Privacy (UK GDPR)

You are collecting names and email addresses, so tell people what for. The registration form already shows the consent line:
the name and avatar appear on the big screen; the organisers keep name and email for this event only; they can cancel any time
(*Cancel my registration* on the page, or you can remove them). Delete the event when it is over, and mention your retention
period and contact in your own privacy notice.
