
# =========================================================
# MeetIQ - AI Meeting Summarizer
# Local AI using Ollama
#
# Drop-in replacement for ai-services/summarization/summarizer.py
# generate_summary(transcript) still returns:
#   {"discussion_points": [...], "decisions": [...], "action_items": [...]}
#
# What changed:
#   - Ollama JSON mode ("format": "json") so the reply is valid JSON
#   - explicit context window (num_ctx) + low temperature
#   - long transcripts are split into chunks, summarised, then merged
#   - real errors are RAISED (Ollama offline, bad reply) instead of
#     silently returning an empty summary that looks like success
# =========================================================
 
import json
import os
import requests
 
OLLAMA_BASE_URL = (
    os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
).rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/generate"
MODEL_NAME = "llama3.2:3b"
 
NUM_CTX = 8192            # context window sent to Ollama
CHUNK_CHARS = 6000        # ~1500 tokens of transcript per chunk
REQUEST_TIMEOUT = 180     # seconds per Ollama call
 
EMPTY_SUMMARY = {
    "discussion_points": [],
    "decisions": [],
    "action_items": [],
}
 
SCHEMA_EXAMPLE = """{
    "discussion_points": ["point 1", "point 2"],
    "decisions": ["decision 1"],
    "action_items": ["action item 1"]
}"""
 
 
class SummarizerError(Exception):
    """Raised when the AI summary could not be produced."""
 
 
# ---------------------------------------------------------
# Ollama call
# ---------------------------------------------------------
 
def _call_ollama(prompt):
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "num_ctx": NUM_CTX,
                    "temperature": 0.2,
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
 
    except requests.exceptions.ConnectionError:
        raise SummarizerError(
            "Could not reach Ollama. Make sure it is running "
            f"(ollama serve) and the model '{MODEL_NAME}' is installed."
        )
    except requests.exceptions.Timeout:
        raise SummarizerError("Ollama took too long to respond.")
    except requests.exceptions.HTTPError as error:
        raise SummarizerError(f"Ollama returned an error: {error}")
 
    return response.json().get("response", "").strip()
 
 
# ---------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------
 
def _clean_list(value):
    """Turn whatever the model returned into a clean list of strings."""
    if not isinstance(value, list):
        return []
 
    cleaned = []
    seen = set()
 
    for item in value:
        if isinstance(item, dict):
            # small models sometimes return {"task": "...", "owner": "..."}
            item = " - ".join(str(v) for v in item.values() if v)
 
        text = str(item).strip()
        key = text.lower()
 
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
 
    return cleaned
 
 
def _parse_summary(response_text):
    """Parse the model reply into the summary structure."""
    text = response_text.strip()
 
    # strip ```json fences if the model added them anyway
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
 
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise SummarizerError("The AI returned a reply that was not valid JSON.")
 
    if not isinstance(data, dict):
        raise SummarizerError("The AI returned an unexpected format.")
 
    return {
        "discussion_points": _clean_list(data.get("discussion_points")),
        "decisions": _clean_list(data.get("decisions")),
        "action_items": _clean_list(data.get("action_items")),
    }
 
 
def _merge(summaries):
    merged = {key: [] for key in EMPTY_SUMMARY}
 
    for summary in summaries:
        for key in merged:
            merged[key].extend(summary.get(key, []))
 
    return {key: _clean_list(values) for key, values in merged.items()}
 
 
def _split_transcript(transcript, max_chars=CHUNK_CHARS):
    """Split on line boundaries so a speaker's sentence is not cut in half."""
    chunks = []
    current = []
    size = 0
 
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
 
        if size + len(line) > max_chars and current:
            chunks.append("\n".join(current))
            current, size = [], 0
 
        current.append(line)
        size += len(line) + 1
 
    if current:
        chunks.append("\n".join(current))
 
    return chunks
 
 
# ---------------------------------------------------------
# Prompts
# ---------------------------------------------------------
 
def _summary_prompt(transcript):
    return f"""
You are an AI meeting assistant for MeetIQ.
 
Analyze the following meeting transcript. Each line is "Speaker: text".
 
Return ONLY valid JSON in exactly this structure:
 
{SCHEMA_EXAMPLE}
 
Rules:
- Only use information present in the transcript. Do not invent anything.
- Every list item must be a short plain-text sentence (not an object).
- For action items, include who is responsible when the transcript says so.
- If there are no decisions, return an empty list.
- If there are no action items, return an empty list.
 
MEETING TRANSCRIPT:
{transcript}
"""
 
 
def _merge_prompt(summary):
    return f"""
You are an AI meeting assistant for MeetIQ.
 
Below are partial notes from different parts of ONE meeting.
Combine them into a single final summary: remove duplicates, merge
similar points, and keep every distinct decision and action item.
 
Return ONLY valid JSON in exactly this structure:
 
{SCHEMA_EXAMPLE}
 
Rules:
- Only use the notes below. Do not invent anything.
- Every list item must be a short plain-text sentence.
 
NOTES:
{json.dumps(summary, indent=2)}
"""
 
 
# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
 
def generate_summary(transcript):
    """
    Generate a structured meeting summary with a local Ollama model.
 
    Returns the summary dict. Raises SummarizerError if the AI could
    not be reached or did not return usable JSON.
    """
    if not transcript or not transcript.strip():
        return dict(EMPTY_SUMMARY, discussion_points=[], decisions=[], action_items=[])
 
    chunks = _split_transcript(transcript)
 
    partial = [_parse_summary(_call_ollama(_summary_prompt(chunk))) for chunk in chunks]
 
    if len(partial) == 1:
        return partial[0]
 
    combined = _merge(partial)
 
    # one final pass so a long meeting reads as one coherent summary
    try:
        return _parse_summary(_call_ollama(_merge_prompt(combined)))
    except SummarizerError:
        return combined
 
 
def summary_has_content(summary):
    """True when at least one list in the summary has something in it."""
    return isinstance(summary, dict) and any(
        summary.get(key) for key in EMPTY_SUMMARY
    )
 
 
# =========================================================
# TEST
# =========================================================
 
if __name__ == "__main__":
 
    test_transcript = """
Asha: Today we reviewed the MeetIQ development progress.
Asha: WebRTC meetings, screen sharing and attention monitoring are done.
Ravi: We decided to build AI meeting summarization next.
Keerthi: I will work on the summarization module.
Asha: The team will test it with real meeting transcripts.
"""
 
    try:
        result = generate_summary(test_transcript)
    except SummarizerError as error:
        print("Summary failed:", error)
    else:
        print("\nAI MEETING SUMMARY\n===================")
        for title, key in [
            ("DISCUSSION POINTS", "discussion_points"),
            ("DECISIONS", "decisions"),
            ("ACTION ITEMS", "action_items"),
        ]:
            print(f"\n{title}:")
            for item in result[key]:
                print("-", item)
 
