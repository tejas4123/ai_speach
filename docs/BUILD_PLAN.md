# 5–8 hour plan: from zip file to a working, explainable demo

The code is written and tested offline. Your hours go into: running it with real APIs, making a real phone call, collecting your own latency numbers, and understanding every file well enough to defend it.

After each block, do the "can I explain it?" check out loud. If you can't, reread that file before moving on.

## Hour 0–0.5: Accounts and keys
- [ ] Deepgram account → API key (free credit covers all testing)
- [ ] Groq account → API key (free) *or* OpenAI key
- [ ] Twilio trial account → buy the free trial number, verify your own mobile, enable India in Voice → Geo permissions
- [ ] ngrok account → install, add auth token
- [ ] `cp .env.example .env` and fill in the keys

## Hour 0.5–1.5: Run it, read the brain
- [ ] `pip install -r requirements-dev.txt` then `pytest -q` (expect 37 passed)
- [ ] `python scripts/chat_cli.py`. Try: order KE1002, returning opened earbuds, EMI, an Aadhaar number, "I want a human"
- [ ] Read `app/agent.py`, `app/tools.py`, `app/rag.py`
- **Can I explain it?** How a streamed tool call arrives in fragments and gets reassembled. Why the filler phrase exists. What BM25 is good at and where embeddings help.

## Hour 1.5–2.5: The voice pipeline, locally
- [ ] Start the server, generate the three sample WAVs, run `scripts/simulate_call.py` with `--interrupt-wav`
- [ ] Listen to `bot_reply.wav`
- [ ] Read `app/session.py` slowly, top to bottom. This is the file interviewers care about.
- **Can I explain it?** What happens, in order, from a Twilio `media` event to bot audio going back. What a `mark` is. What happens on barge-in, and why history only keeps played sentences.

## Hour 2.5–3.5: A real phone call
- [ ] `ngrok http 8000`, put the host in `PUBLIC_HOST`, restart the server
- [ ] Trigger `POST /call` to your mobile (see README) and talk to it
- [ ] Try: interrupting mid-sentence, pausing mid-question, asking for an order that doesn't exist, background noise
- [ ] Look at `logs/transcripts/*.json` and `logs/latency.jsonl`
- If something breaks, the server log says which stage. Paste the error back to me.

## Hour 3.5–4.5: Experiments = interview material
Run 5+ turns per setting and write the numbers down. Real numbers beat any memorised answer.
- [ ] `ENDPOINTING_MS` = 200 vs 300 vs 600: how often does it cut you off? How does latency change?
- [ ] LLM: Groq (llama-3.3-70b) vs OpenAI (gpt-4o-mini): time to first token
- [ ] TTS: Deepgram Aura vs ElevenLabs flash: time to first byte, and which sounds better on a phone line
- [ ] `python scripts/latency_report.py` → paste the table into the README

## Hour 4.5–5.5: Indian-language experiment
- [ ] `STT_MODEL=nova-3`, `STT_LANGUAGE=multi`, `TTS_PROVIDER=elevenlabs`. Speak Hinglish ("mera order kab aayega, KE1002")
- [ ] Note what breaks: STT errors, order ID recognition, pronunciation of rupee amounts. That list is a strong interview answer by itself.

## Hour 5.5–6: Ship it
- [ ] Push to GitHub (github.com/tejas4123). Keep `.env` and `docs/INTERVIEW_NOTES.md` out of the repo
- [ ] Record a 1–2 minute screen + audio demo of a real call (include one interruption) and link it in the README
- [ ] Add the project to your resume and LinkedIn

## Hours 6–8 (stretch, pick one)
- [ ] **Silero VAD**: `pip install silero-vad`, set `VAD_PROVIDER=silero`, compare false barge-ins in a noisy room
- [ ] **Hybrid RAG**: `EMBEDDINGS_ENABLED=true` with an OpenAI key; find a query BM25 misses and hybrid gets right
- [ ] **Deploy**: Docker on an AWS EC2 instance (you've used EC2) with a real domain instead of ngrok
- [ ] **Post-call summary**: after `stop`, ask the LLM for a 3-line summary + sentiment and save it with the transcript (in production: a Celery task)
