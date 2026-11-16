# duemail

Turn deadlines buried in your Gmail into Google Calendar events.

- **Automatic scan** – reads recent mail and finds deadlines such as
  "complete the assessment within 7 days", "due by Nov 23 at 11:59 PM PST",
  "reply by Friday", "registration closes on 2026-12-15".
- **Force label** – put any mail under your `Deadline` label and it always gets
  an event, even if the scan missed it.
- **Label styles** – a mail's Gmail label decides the event's colour, title
  prefix, reminders, calendar and work block (`Jobs` = red with a 3-hour block,
  `Bills` = yellow with no block, ...).
- **Two events per deadline** – the deadline itself, plus a "Work on: ..." time
  block a few days earlier.
- Runs on your own computer. It can read mail but cannot change or send it.
  Nothing leaves your machine except calls to Google's own APIs.

## Install

Needs Python 3.10+.

```
pip install .            # from this folder
duemail init            # creates ~/.duemail/config.yaml
```

## Google setup (once per person, about 5 minutes)

Each user signs in through their own Google Cloud project. Google does not let
an unverified shared app read Gmail for the general public (see "Sharing with
other people" below), so this is the dependable route.

1. Open https://console.cloud.google.com and create a project.
2. In **APIs & Services > Library**, enable **Gmail API** and **Google Calendar API**.
3. In **APIs & Services > OAuth consent screen**, choose **External**, fill in the
   app name and your email, and add your own Gmail address as a **test user**.
4. In **Credentials > Create credentials > OAuth client ID**, pick **Desktop app**.
   Download the JSON and save it as `~/.duemail/credentials.json`
   (Windows: `C:\Users\<you>\.duemail\credentials.json`).
5. Run `duemail auth`. A browser opens; sign in and allow access.

**Avoid signing in again every week:** while the consent screen is in
*Testing* status, Google expires the sign-in after 7 days. Set the publishing
status to **In production** (no verification needed for personal use; you will
see an "unverified app" warning once when signing in) and the sign-in lasts.

## Use

```
duemail run --dry-run     # show what would be added, change nothing
duemail run               # add events for new deadlines
duemail run --days 14     # look further back the first time
duemail watch             # keep running, check every 15 minutes
duemail labels            # list your Gmail labels
duemail test "Please submit the form by Dec 3 at 5pm"   # try the detector
```

Mails are remembered in `~/.duemail/state.db`, so running again never creates
duplicates. Delete that file to start over.

### Run it automatically

- **Windows** – Task Scheduler > Create Basic Task > trigger "Daily", repeat
  every 30 minutes > action "Start a program": `duemail` with argument `run`.
- **macOS / Linux** – `crontab -e` and add
  `*/30 * * * * duemail run >> ~/.duemail/log.txt 2>&1`
  (use the full path from `which duemail`).

## Configure (`~/.duemail/config.yaml`)

```yaml
scan:
  query: "newer_than:3d -in:spam -in:trash -in:sent -in:drafts"   # any Gmail search
  min_confidence: 0.6        # 0.85 = only clear-cut deadlines
  ignore_labels: ["Newsletters"]

force:
  label: "Deadline"          # create this label in Gmail
  default_days: 7            # used when a forced mail has no date in it

defaults:
  prefix: "DUE: "
  remind_days_before: [1]
  work_block: {enabled: true, days_before: 2, start: "18:00", hours: 2}

label_rules:                 # first matching label wins
  - label: "Jobs"            # also matches Jobs/Assessments etc.
    color: tomato
    prefix: "JOB DUE: "
    remind_days_before: [1, 3]
    work_block: {days_before: 3, hours: 3}
  - label: "Bills"
    color: banana
    calendar: primary        # or a calendar ID from Calendar settings
    work_block: {enabled: false}
```

Colours: lavender, sage, grape, flamingo, banana, tangerine, peacock, graphite,
blueberry, basil, tomato.

How events are shaped:

| Mail says | Deadline event | Reminders |
|---|---|---|
| a date, no time ("within 7 days") | all-day on the due date | 9:00, N days before |
| a date and time ("by Nov 20, 5pm") | 30 minutes ending at the deadline | N days before, plus 2 hours before |

The work block is skipped when it would not fit before the deadline.

### Smarter detection with a local AI model (optional)

The built-in detector is pattern-based: fast, offline, and it will miss unusual
wording. For better recall, install [Ollama](https://ollama.com), pull a model,
and set:

```yaml
extractor: llm
llm: {base_url: "http://localhost:11434/v1", model: "llama3.1"}
```

Any OpenAI-compatible endpoint works. If you use a hosted one, the text of your
mails is sent to that provider.

## Sharing with other people

Send them this folder (or publish it on GitHub / PyPI); each person does the
"Google setup" above with their own account. If you would rather ship one
shared sign-in so users skip that step, Google requires the app to pass OAuth
verification, and because reading Gmail is a *restricted* scope that includes
a security assessment. Until then a shared app is capped at 100 users in total
and shows the unverified-app warning.

## Development

```
pip install -e ".[dev]"
pytest
```

- `duemail/extract.py` – deadline detection rules (add patterns here)
- `duemail/llm.py` – optional AI extractor
- `duemail/google_api.py` – Gmail and Calendar calls
- `duemail/core.py` – config, label rules, event building, the run loop
- `duemail/cli.py` – commands

MIT licence.
