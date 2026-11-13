# VaaniSetu AI: Multilingual, Voice-First Welfare & Grievance Assistant

> A production-grade AI assistant modeled on India's Digital Public Infrastructure. Citizens speak or type in Hindi, English or Hinglish to understand PM-KISAN and Ayushman Bharat PM-JAY, check their eligibility step by step, and prepare CPGRAMS grievances. It is built with LangGraph state orchestration, hybrid retrieval with citations, Indian PII masking and a human-in-the-loop confirmation gate.

*Vaani* means voice and *Setu* means bridge. VaaniSetu AI is a private assistant: you ask in your own language, by voice or text, and get answers taken only from the official scheme rules, with the exact rule each answer comes from. When you check eligibility or prepare a complaint, it asks one simple question at a time, shows you a summary, and only finalises after you confirm.

---

## What you can do

- **Create an account** with your name, email, phone number and password. Your email is confirmed with a 6-digit code.
- **Ask in your own language**: English, Hindi (Devanagari), Hinglish or another Indian language. The reply comes back in the language you used, or in the one you choose.
- **Speak instead of typing.** Press the mic and ask your question. VaaniSetu shows you what it heard first, so you can fix a word or record again before anything is sent, and the answer is read aloud.
- **Listen to any answer.** Every reply has a **Listen** button that reads it aloud, whether you typed or spoke the question.
- **Get answers from the official rules** of PM-KISAN and Ayushman Bharat PM-JAY, each with the section it comes from (for example *PM-KISAN §2.1 Amount of benefit*). If the rules don't cover a question, VaaniSetu says so instead of guessing.
- **Check your eligibility** for PM-KISAN or PM-JAY. Give details in any order, several at once; it asks only for what's missing. The verdict (*Eligible*, *Likely eligible*, *Needs verification* or *Not eligible*) comes with the reasons, the rules applied and the next steps.
- **Prepare a public grievance for CPGRAMS.** Describe the problem in your own words; VaaniSetu works out the right department, category and urgency, writes a clear summary, and gives you a ready-to-file grievance.
- **Review before anything is final.** Every check and grievance shows a summary first. Confirm, correct something ("the land is actually 3 hectares"), or cancel.
- **Keep your results** under **My applications**: readable summaries plus portal-ready JSON to download.
- **Pick up where you left off.** Conversations are saved, even across restarts.
- **Keep a profile** with your photo, date of birth, gender and preferred reply language.
- **Reset a forgotten password** with a code sent to your email.

## How it works

When you send a message, VaaniSetu:

1. **Listens** (voice only). Your recording is turned into text by OpenAI's speech model, or by Bhashini if you configure it.
2. **Masks your personal details first.** Aadhaar numbers (checked with the official Verhoeff checksum), PAN, mobile numbers, bank accounts, UPI IDs, emails and names are replaced with placeholders such as `<AADHAAR_1>` **before** anything reaches the AI model. The masking runs on your own computer.
3. **Understands what you want.** An AI router decides whether you're asking a question, checking eligibility, filing a grievance or asking something out of scope, rewrites follow-ups into a complete question, and detects your language.
4. **For questions: searches the official rules.** It combines keyword search (BM25) and meaning-based search (vector embeddings in Qdrant), merges them with Reciprocal Rank Fusion, and writes an answer that cites the sections it used. If the first passages don't contain the answer, it searches wider once before giving up. Citations to sections that weren't actually retrieved are rejected.
5. **For eligibility and grievances: collects the details.** The AI reads what you said (*"2 acre"* becomes 0.81 hectares; *"10 hazaar mahina"* becomes ₹1,20,000 a year) and every value is validated before it's accepted. Your occupation is matched against the occupations named in the official rules themselves, with a second, more careful AI check when the first pass isn't sure.
6. **Asks you to confirm.** The conversation pauses at a confirmation step; nothing is finalised until you say yes.
7. **Applies the rules.** Eligibility verdicts come from a fixed rule engine that encodes the policy documents, never from the AI's opinion, and every reason cites the rule behind it.
8. **Replies in your language** (translated by the AI, or by Bhashini) and reads it aloud if you've turned on spoken replies.

Every step is timed, so each answer can show what it took: privacy masking, rule search, AI reasoning, translation and speech.

### What to expect

VaaniSetu is measured on a fixed set of 30 real-world conversations: plain English and Hinglish questions, Hindi in Devanagari, step-by-step eligibility checks with noisy code-mixed answers ("2 acre", "10 hazaar mahina", "haan"), tricky exclusions (a government teacher who owns farmland, a tenant farmer), grievances, and questions the rules don't cover or that try to misuse the assistant.

<!-- BENCHMARK:START -->
_Run 2026-11-13 · model `gpt-4.1-mini` · 30 cases / 58 turns · `python scripts/run_eval.py`_

| Metric | Score |
|---|---|
| Faithfulness (supported claims / claims) | 1.00 |
| Answer relevancy (LLM judge, 0–1) | 1.00 |
| Citation accuracy (expected section cited) | 1.00 |
| Retrieval hit-rate@k (hybrid RRF) | 1.00 |
| Intent routing accuracy | 1.00 |
| Slot-extraction accuracy (field level) | 1.00 |
| Eligibility verdict accuracy | 1.00 |
| Grievance payload completion | 1.00 |
| Refusal accuracy (unanswerable) | 1.00 |
| Latency p50 / p95 per turn | 3107 ms / 6353 ms |
| LLM calls / tokens per turn | 2.4 / 2211 |
<!-- BENCHMARK:END -->

In short:

- **Answers stay inside the official rules.** Every claim in every answer was backed by the cited section, and questions the rules don't cover (such as Kisan Credit Card interest rates) get an honest "not found" with the right helpline.
- **Eligibility verdicts were right every time**, including the exclusion edge cases, because they come from the rule engine rather than the AI.
- **Hinglish and Hindi work as well as English**: the details were read correctly from code-mixed answers, and replies came back in the language and script the citizen used.
- **A typical reply takes about 3.5 seconds** (about 6 seconds for the slowest), using 2-3 AI calls.

Faithfulness and answer relevancy follow the RAGAS definitions, scored by an AI judge; the other scores are exact matches against the expected result.

---

## Requirements

- **Windows, macOS or Linux**
- **Python 3.11 or newer:** https://www.python.org/downloads/
- **Node.js 20 or newer:** https://nodejs.org
- **An OpenAI API key** (required; also powers voice): https://platform.openai.com/api-keys
- **A Gmail account** (recommended, for sending verification emails)
- **Bhashini keys** (optional alternative voice provider): https://bhashini.gov.in/ulca

## Installation

Open a terminal in the project folder and run the commands below. They're shown for Windows; on macOS and Linux, use `.venv/bin/python` and `cp` instead.

**1. Install the Python packages**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**2. Build the web interface**

```powershell
cd frontend
npm install
npm run build
cd ..
```

**3. Create your settings file**

```powershell
copy .env.example .env
```

Then open `.env` and fill in your keys, as described below.

## Configuration

All settings live in the `.env` file in the project folder. It contains your private keys, so never share it or upload it anywhere. The repository only includes `.env.example`, which shows the format.

### API key

```env
OPENAI_API_KEY=sk-...        # required: answers, translation, voice
```

Voice works with this key alone (`gpt-4o-mini-transcribe` to understand speech, `gpt-4o-mini-tts` to speak replies, together well under a rupee per minute). To use the Government of India's Bhashini service instead, add `SPEECH_PROVIDER=bhashini` with `BHASHINI_USER_ID` and `BHASHINI_ULCA_API_KEY`.

### Email (verification codes and password resets)

VaaniSetu sends emails through Gmail:

1. Go to https://myaccount.google.com/security and turn on **2-Step Verification**.
2. Go to https://myaccount.google.com/apppasswords, create an app password named `VaaniSetu`, and copy the 16-character password.
3. Add these lines to `.env`:
   ```env
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=you@gmail.com
   SMTP_PASSWORD=abcd efgh ijkl mnop
   SMTP_FROM=VaaniSetu AI <you@gmail.com>
   ```
4. Check that it works:
   ```powershell
   .venv\Scripts\python -m server.mailer you@example.com
   ```

If email isn't set up, VaaniSetu still works: the codes are printed in the server window instead of being emailed. That's handy when you're trying it on your own computer.

## Start the app

```powershell
.venv\Scripts\python -m server
```

Your browser opens **http://localhost:8000**. The first start takes a little longer while VaaniSetu loads the official scheme rules into its search index, and a loading screen shows while that happens. To stop the app, press **Ctrl + C** in the terminal.

## Using VaaniSetu

1. **Create an account.** Click *Create an account* and enter your name, email, phone and a password.
2. **Confirm your email.** Enter the 6-digit code from the email (check your spam folder).
3. **Ask anything.** Type, tap one of the suggestions, or press the mic and speak, then press **Done**. Check the question shown in the box (edit it, **Record again** or **Discard**) and press **Send**. Try *"PM-KISAN mein saal ka kitna paisa milta hai?"*. Press **Listen** under any answer to hear it.
4. **Check eligibility.** Say *"Am I eligible for Ayushman Bharat?"* and answer the questions. When the summary appears, tap **Yes, confirm**, **Change something** or **Cancel**.
5. **Prepare a complaint.** Describe the problem, for example *"Meri PM-KISAN ki kist 6 mahine se nahi aayi"*. When it's ready, use **Copy complaint text** and **Open CPGRAMS portal** to file it.
6. **Find your results** under **My applications**, where you can also download the portal-ready JSON.
7. **Choose how replies come back** with **Reply in** at the top of the chat, and turn on **Speak replies** to hear answers.

Each reply has a **How this answer was made** section showing what was masked, how the message was understood, and how long each step took.

## Privacy and security

- Aadhaar, PAN, phone, bank, UPI and email details, and names, are masked on your computer before any text is sent to OpenAI. The AI only ever sees placeholders.
- Conversations and applications are stored **only in masked form**. Your own screen shows what you typed, but the database never holds those numbers.
- Your conversations, applications and profile are visible only to your account.
- Passwords are stored securely hashed. Email codes expire after 10 minutes and stop working after 5 wrong attempts; repeated failed sign-ins are temporarily blocked.
- Voice recordings are sent to the speech provider (OpenAI or Bhashini) to be transcribed; the transcript is masked before the AI assistant reads it.
- Everything else is stored on the computer running VaaniSetu, in the `data/` and `qdrant_data/` folders.

## Troubleshooting

| Problem | What to do |
|---|---|
| "The assistant isn't set up yet" | Add your `OPENAI_API_KEY` to `.env` and restart the app. |
| Replies come back in English | Set **Reply in** to *Same as I write*, and make sure `.env` has `DEFAULT_LANGUAGE=auto`. |
| The mic button is missing | Browsers only allow the microphone on `localhost` or HTTPS. Open http://localhost:8000. |
| "Microphone access was blocked" | Allow the microphone for this site in the browser's address-bar settings. |
| No verification email | Check the spam folder, then use "send a new code". Run `python -m server.mailer you@example.com` to test your email settings. |
| "Storage folder ... is already accessed by another instance" | Only one copy of the app (or the evaluation) can use the search index at a time. Close the other one. |
| The page doesn't load | Make sure the app is running in the terminal and open http://localhost:8000. |
| Port 8000 is already in use | Start with `.venv\Scripts\python -m server --port 8001`. |

---

## For developers

Run the backend and frontend separately, with live reload:

```powershell
.venv\Scripts\python -m server --reload --no-browser    # API at http://localhost:8000 (docs at /api/docs)
cd frontend; npm run dev                                # UI at http://localhost:5173
```

Measure answer quality on the 30-case set and refresh the table above (stop the app first):

```powershell
.venv\Scripts\python scripts\run_eval.py --update-readme
```

Project structure:

```text
server/      Web API: accounts, email codes, sessions, profile, conversations, streaming chat & voice, applications
frontend/    React + TypeScript interface (Vite)
src/         The assistant: PII guardrail, speech, hybrid retrieval, LangGraph agent, eligibility rule engine
config/      Settings, all adjustable from .env
data/        Curated policy documents (numbered sections are the citation ids) and the 30-case evaluation set
scripts/     Evaluation runner
```

Design decisions are recorded in [CLAUDE.md](CLAUDE.md).

Built with LangGraph, LangChain, OpenAI, Qdrant, BM25, Presidio, spaCy, FastAPI, SQLAlchemy and React.

## Disclaimer

The policy documents are curated summaries of official guidelines, prepared for this project. VaaniSetu AI is not affiliated with the Government of India. Always confirm on [pmkisan.gov.in](https://pmkisan.gov.in), [pmjay.gov.in](https://pmjay.gov.in) and [pgportal.gov.in](https://pgportal.gov.in).
