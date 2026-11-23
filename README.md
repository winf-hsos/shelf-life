# Shelf Life

A lecture game about AI agents for the lecture series on food loss and waste. Each evening, an AI
agent, the *Night Shift Agent*, runs a corner shop on its own for one week (Monday to Saturday). The
students shop there on their phones, each with €28 for the whole week (set on `/director`; changed during
a week, everyone gets the difference). Only complete
meals score points, and food that spoils in their fridge costs points.

The agent works in a visible loop. It has a goal, it reads a wake-up message, it looks things up with
tools, gives a reason for every action and acts through tools. On Friday the food bank's booking page
carries a hidden instruction (a prompt injection), and the agent throws good dairy away. Friday can
then be played again with the owner's approval for risky actions.

## Run it

```bash
pip install -r requirements.txt
python -m shelflife.server
```

The server prints three addresses:

| Page | Who | What |
|---|---|---|
| `/` | students | join with a nickname, buy, watch the fridge and meals |
| `/beamer#key=…` | projector | the shop, the agent and, with the key, the director's bar at the bottom edge |
| `/director#key=…` | lecturer's phone | the same controls as the bar |

Environment variables:

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` | key for the language model; without it only the fixed rule runs |
| `SHELFLIFE_MODEL` | model name, default `gpt-6-luna` |
| `SHELFLIFE_MODELS` | models the director may choose from, comma-separated, default `gpt-6-luna,gpt-6-sol,gpt-6-terra` |
| `SHELFLIFE_ADMIN_KEY` | director key; a random one is printed if unset |
| `SHELFLIFE_MODEL_TIMEOUT` | seconds per model request, default 30 |
| `SHELFLIFE_PUBLIC_URL` | join link for the QR code; defaults to this computer's LAN address |
| `PORT` | default 8010 |

## Hosting

For the lecture it runs on the university's Uberspace asteroid (SSH alias `uberspace-hsos`) at
https://shelflife.hsos.uber.space as the systemd user service `shelf-life` on port 8030, with the
working directory `~/shelf-life` and the virtualenv `~/venv-shelflife`. The unit is
`deploy/shelf-life.service`. The environment file `~/.config/shelf-life.env` (chmod 600) holds
`OPENAI_API_KEY` and `SHELFLIFE_ADMIN_KEY`.

First-time setup:

    ssh uberspace-hsos 'python3 -m venv venv-shelflife && mkdir -p shelf-life .config/systemd/user'
    sh deploy/deploy.sh || true      # copies the files; the restart fails until the service exists
    ssh uberspace-hsos 'venv-shelflife/bin/pip install -q -r shelf-life/requirements.txt'
    scp deploy/shelf-life.service uberspace-hsos:.config/systemd/user/
    ssh uberspace-hsos 'install -m 600 /dev/null .config/shelf-life.env'
    # then, on the server, write OPENAI_API_KEY=... and SHELFLIFE_ADMIN_KEY=... into that file
    ssh uberspace-hsos 'systemctl --user daemon-reload && systemctl --user enable --now shelf-life'
    ssh uberspace-hsos 'uberspace web domain add shelflife.hsos.uber.space'
    ssh uberspace-hsos 'uberspace web backend add shelflife.hsos.uber.space/ port 8030'

For updates, run `sh deploy/deploy.sh`. A restart clears joined phones and the week.
After the lecture: `systemctl --user disable --now shelf-life`.

## An evening

1. **Morning:** the delivery arrives; goods left from earlier evenings are older now.
2. **20:00 check-in:** the agent reads its wake-up message (with its note from yesterday) and acts.
3. **Shop open** (60 s by default): phones, simulated students and regulars buy.
4. **21:00 check-in:** last chance to book the food bank and change prices.
5. **Shop open** (60 s).
6. **22:00 check-in:** the agent hands on (food bank, or up to 5 items to the cashier, who takes goods only now), keeps or bins what is left; everything else that cannot be
   sold on the next opening day goes in the bin. Then every student cooks.
7. **Night:** the summary of the evening, until the lecturer starts the next evening.

Before and after every check-in the evening stops until the lecturer goes on, so the room can talk about
what the agent is about to do and what it did. The setting *Stop before and after each check-in* switches
this off.

The agent knows the purchase price: the shop pays half the shelf price for everything that leaves the
shelf, sold, given away or binned. At 22:00 `look_at_shelf` marks for every pack whether it can stay.
Markdowns go in steps of 5 %, from 5 to 90 % off; between two tool calls the agent waits 3 s by
default, so the room can follow. The agent orders whatever it wants for the next morning, up to twice
the largest standing order of a product; there is no minimum.

One evening is one conversation with the model. The next evening starts with an empty input, so the
request stays short and fast; what reaches back is the agent's note for tomorrow and `sales_report`,
which lists every earlier evening of the week product by product (delivered, sold, customers who found
none, binned).

## Director

The key is entered once per browser: open a page with `#key=…`, or press K on the projector page or answer the
prompt on `/director`. The browser keeps it (localStorage) and drops it from the address bar; a wrong key is
forgotten and asked for again.

The big button on `/director`, and the first button of the projector's bar, always names the next step
(*Start the 21:00 check-in*, *Open the shop until 22:00*, *Start Tuesday evening*); while the evening waits,
the projector's bar stays visible.

*Show the ranking* lists every student on a phone (simulated students are left out) with points, dinners,
breakfasts, spoiled items and money left, across the whole projector; it closes when the evening goes on.

Keyboard on the projector page with the key: Space goes on (or closes the shop early while it is open),
L ranking, P pause, F fast forward, A owner approval, R reveal the food bank page, C context, M music, S sounds, D pin the bar,
H hide the bar completely and show it again (the browser remembers the choices).

*Sound* plays only on the projector page opened with the director key, so a student who opens `/beamer`
on a laptop hears nothing. Everything is synthesised in the browser (`static/sound.js`, Web Audio), without
audio files or network: a slow lo-fi loop that gets drums while the shop is open and fades back during a
pause or the tour; a robot chirp when the agent wakes and its own sound for every tool; a shop bell with
students murmuring when the shop opens and when customers walk in, louder murmur when a phone joins; the
till, the delivery van, the owner's alert and stamp, a drum roll for the ranking. Browsers start audio
only after a click or key press on the page.

*Context* (C, or the dashed button after the agent's tools) shows the request the model receives, as JSON
for the Responses API: model, instructions, the eleven tools, and the input of this evening, which grows by
two items with every tool call. It refreshes while it is open; Esc closes it.

A pause shows a banner and a yellow frame on the projector and a notice on every phone; the clock and
the shop stand still until it ends. A click on a tool call in the agent's panel opens it in large print
with the calls before and after it; the arrow keys page through the evening, Esc closes it. A click on
one of the agent's tools below its speech bubble opens a small card with what the agent reads about the
tool, an example call and its answer; the tool lights up, and the arrow keys page through all eleven.

- **Owner approval:** binning more than 10 items or a markdown above 50 % waits for *Approve* or
  *Reject* on the projector or the director's phone.
- **Injection:** the day on which the food bank's confirmation page carries the hidden text (default
  Friday). It is set only on `/director`, so the room never sees it on the projector.
- **Language model:** chosen on `/director` from `SHELFLIFE_MODELS`. Each choice is tried with a short
  request first; a model the key cannot use is refused with the reason, and the old one stays.
- **Model outage:** if the language model does not answer (30 s per request, one retry), the check-in
  ends undone and the projector says so; the evening goes on without the agent. Every later check-in
  tries again.
- **Play this evening again:** restores the shop and all students as they were before 20:00 and switches
  approval on. Purchases made during the first run are undone.

Phones that join while the week runs replace a simulated student. Phones that have not been seen for
45 seconds shop on autopilot. Nothing about players is written to disk.

## Files

| File | Content |
|---|---|
| `shop.py` | the world: products, deliveries, ageing, customers, tools of the agent, cooking and points |
| `agent.py` | the language model in a tool loop, and a fixed rule for tests without the API |
| `bots.py` | simulated students |
| `server.py` | the live game: phases, phones, projector, director, approval |
| `static/` | the three pages and the images |
| `static/img/src/` | the generated originals with their prompts (`.prompt.txt`); `scripts/prepare_images.py` makes the web versions (needs Pillow) |

`scripts/run_week.py` plays a week without a browser, for calibration.

The images are AI-generated (GPT-Image 2.5) and carry C2PA metadata.

## Licence

MIT, see `LICENSE`.
