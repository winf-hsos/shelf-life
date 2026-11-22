# Shelf Life

A lecture game about AI agents for the lecture series on food loss and waste. Each evening, an AI
agent, the *Night Shift Agent*, runs a corner shop on its own for one week (Monday to Saturday). The
students run shared flats on their phones and shop there with €28 for the whole week. Only complete
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
| `/` | students | join with a flat name, buy, watch the fridge and meals |
| `/beamer#key=…` | projector | the shop, the agent and, with the key, the director's bar at the bottom edge |
| `/director#key=…` | lecturer's phone | the same controls as the bar |

Environment variables:

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` | key for the language model; without it only the fixed rule runs |
| `SHELFLIFE_MODEL` | model name, default `gpt-6-luna` |
| `SHELFLIFE_ADMIN_KEY` | director key; a random one is printed if unset |
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
3. **Shop open** (40 s): phones, simulated students and regulars buy.
4. **21:00 check-in:** last chance to book the food bank and change prices.
5. **Shop open** (40 s).
6. **22:00 check-in:** the agent hands on, keeps or bins what is left; everything else that cannot be
   sold on the next opening day goes in the bin. Then every flat cooks.
7. **Night:** the summary of the evening, until the lecturer presses *Next evening*.

## Director

Keyboard on the projector page with the key: Space next evening or close the shop now, P pause, F fast
forward, A owner approval, R reveal the food bank page, D pin the bar.

- **Owner approval:** binning more than 10 items or a markdown above 50 % waits for *Approve* or
  *Reject* on the projector or the director's phone.
- **Injection:** the day on which the food bank's confirmation page carries the hidden text (default
  Friday).
- **Play this evening again:** restores the shop and all flats as they were before 20:00 and switches
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
