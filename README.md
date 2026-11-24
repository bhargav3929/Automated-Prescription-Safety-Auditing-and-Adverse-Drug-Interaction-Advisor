# Automated Prescription Safety Auditing and Adverse Drug Interaction Advisor

Enter two or more prescribed medicines and this app checks every pair against official FDA drug labels. It shows which combinations are risky, explains why in plain English, and gives you questions to ask your doctor or pharmacist. It is built for students, patients and caregivers who want to understand a prescription list.

**It is educational decision-support only. It is not medical advice. Always ask a licensed physician or pharmacist before starting, stopping or changing any medicine.**

## 1. What you need

- **Python 3.10 or newer.** Download it from https://www.python.org/downloads/ (on Windows, tick "Add python.exe to PATH").
- **A free Groq key.** Get one at https://console.groq.com/keys. It takes about one minute and needs no card.
- An internet connection. The app reads the free public NLM RxNorm and openFDA services. They need no key.

## 2. Run it (one command)

- **macOS / Linux:** `chmod +x start.sh && ./start.sh`
- **Windows:** double-click `start.bat`

The first run creates a private Python environment, installs the requirements and creates `.env`. The browser then opens at **http://127.0.0.1:8009**. Press `Ctrl+C` (or close the window) to stop.

## 3. Add your Groq key

Click **API Keys** in the top-right of the app and paste your key (it starts with `gsk_`). It is saved only on your computer. You can also open `.env` and set `GROQ_API_KEY=gsk_your_key`, then restart.

## 4. How to use it

1. Click one of the **sample regimens**, such as *Warfarin + Aspirin + Ibuprofen*. The saved result opens instantly and uses none of your runs.
2. To try your own list, type a medicine name and press **Enter**. Each chip shows its RxNorm code (RxCUI) once the name is recognised. You can also click **Paste a list** and paste lines like `Warfarin 5 mg, Aspirin 81 mg`.
3. Choose the patient context (older adult, kidney or liver sensitivity, pregnancy, or your own note).
4. Click **Audit this regimen**. You get a safety score, a network diagram and a matrix of every drug pair. Click any pair to read why it happens, the symptoms to watch for, the FDA label excerpts and the FDA adverse-event report counts.
5. **Safer Alternative Explorer** compares your list before and after swapping one medicine. **Pharmacist Question Sheet** turns the questions into a printable checklist.
6. **History** keeps your recent audits. You get 15 live audits per hour. A run that fails, or a name that cannot be verified, does not use one up.

**Good to know:** It reads label text and adverse-event counts. It does not know your doses, history or other conditions, and it cannot replace a pharmacist's review.

## 5. How it works

1. `app/clinical.py` cleans each name and looks it up in RxNorm (`rxnav.nlm.nih.gov`). It then downloads the openFDA drug label for each medicine and reads the boxed warning, contraindications, drug interactions and warnings sections. A name that RxNorm or openFDA does not know is rejected before anything else happens.
2. The same file cross-matches every pair of labels. If one label mentions the other drug, or its drug class, the sentence is kept and a severity is set by fixed rules. It also fetches how many FDA adverse-event (FAERS) reports list both drugs.
3. `app/pipeline.py` builds the safety score and sends the evidence to Groq. The AI only explains in plain English. It cannot change a severity.
4. `app/ai_groq.py` makes every Groq call, with a timeout, retries and a backup model.
5. `app/access.py` and `app/db.py` count your 15 hourly runs in a local SQLite file and give a run back when something fails.
6. `app/main.py` serves the pages and the API. The screens are in `static/` (`index.html`, `styles.css` and the scripts in `static/js/`).

## 6. Project structure

```
09_prescription_safety_drug_interaction/
├── start.sh                 one-command start (macOS / Linux)
├── start.bat                one-command start (Windows)
├── requirements.txt
├── .env.example             settings (copied to .env on first run)
├── app/
│   ├── main.py              web server and API routes
│   ├── clinical.py          RxNorm + openFDA lookups and pair matching
│   ├── pipeline.py          audit, safety score and the alternative comparison
│   ├── ai_groq.py           all Groq calls
│   ├── access.py            hourly run limit
│   ├── db.py                SQLite storage
│   ├── config.py            reads .env
│   └── errors.py
├── static/
│   ├── index.html
│   ├── styles.css
│   └── js/                  api.js, network.js, render.js, app.js
├── samples/                 4 sample regimens
│   └── results/             saved results for each sample
└── scripts/record_samples.py
```

## 7. Troubleshooting

- **Port already in use:** another program is using port 8009. Close it, or change `PORT` in `.env` and restart.
- **"The AI service is busy right now":** Groq is overloaded. Wait a minute and try again. Your run is not used up.
- **"Your Groq key was rejected":** open **API Keys** and paste a fresh key from https://console.groq.com/keys.
- **"Couldn't verify one or more medication names":** check the spelling. Use the generic name, such as `ibuprofen`, if a brand name is not recognised.
- **Python not found:** install Python 3.10 or newer from https://www.python.org/downloads/ and run the start script again.
- **Install failed on a slow connection:** check your internet and run the start script again. It continues where it stopped.
- **"drug databases are busy":** the free RxNorm or openFDA service is slow. Wait a minute and try again.
