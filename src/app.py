import hashlib
import json
import os
import pathlib
import threading
import time
import typing
import uuid

import fastapi
import fastapi.responses
import pydantic

BASE_DIR = pathlib.Path(__file__).parent
DATA_FILE = BASE_DIR / 'data' / 'state.json'
MAX_NOTE_LENGTH = int(os.environ.get('MAX_NOTE_LENGTH', '200'))

app = fastapi.FastAPI(title='Voting Tool')

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NoteSummaryResponse(pydantic.BaseModel):
  id: str
  text: str
  up: int
  down: int


class BoardSessionRequest(pydantic.BaseModel):
  transaction_ids: list[str] = pydantic.Field(default_factory=list)


class BoardSummaryResponse(pydantic.BaseModel):
  notes: list[NoteSummaryResponse]
  upvoted: list[str]
  downvoted: list[str]


class CreateNoteRequest(pydantic.BaseModel):
  text: str

  @pydantic.field_validator('text')
  @classmethod
  def validate_text(cls, v: str) -> str:
    v = v.strip()
    if not v:
      raise ValueError('text must not be empty')
    if len(v) > MAX_NOTE_LENGTH:
      raise ValueError(f'text must be at most {MAX_NOTE_LENGTH} characters')
    if '\n' in v or '\r' in v:
      raise ValueError('text must be a single line')
    return v


class CreateNoteResponse(pydantic.BaseModel):
  status: typing.Literal['ok'] = 'ok'


class VoteRequest(pydantic.BaseModel):
  note_id: str
  value: typing.Literal[-1, 0, 1]
  replaces: str | None = None


class VoteResponse(pydantic.BaseModel):
  status: typing.Literal['ok'] = 'ok'
  tx_id: str


class ConfigResponse(pydantic.BaseModel):
  max_note_length: int


# ---------------------------------------------------------------------------
# Storage.
#
# `notes`: note_id -> {id, text, hash, up, down, ts}
# `votes`: tx_id -> {note_id, value, superseded}
#
# A vote transaction represents the *current* state a client has set for
# one note. When a client changes its vote it supplies the tx_id of its
# previous vote on that note via `replaces`; the old transaction is then
# marked superseded and its contribution to the note's counters is
# reversed. tx_ids are only ever handed back to the client that created
# them - they are never broadcast to other clients.
# ---------------------------------------------------------------------------

lock = threading.Lock()
notes: dict[str, dict] = {}
note_hashes: set[str] = set()
votes: dict[str, dict] = {}


def _load() -> None:
  global notes, note_hashes, votes
  if DATA_FILE.exists():
    try:
      data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
      data = {}
  else:
    data = {}

  notes = data.get('notes', {})
  votes = data.get('votes', {})
  note_hashes = {note['hash'] for note in notes.values()}


def _save() -> None:
  DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
  tmp_path = DATA_FILE.with_suffix('.tmp')
  tmp_path.write_text(json.dumps({'notes': notes, 'votes': votes}, indent=2), encoding='utf-8')
  tmp_path.replace(DATA_FILE)


_load()


def _apply_vote_delta(note: dict, value: int, sign: int) -> None:
  if value == 1:
    note['up'] += sign
  elif value == -1:
    note['down'] += sign


def _note_summary(note: dict) -> dict:
  return NoteSummaryResponse(id=note['id'], text=note['text'], up=note['up'], down=note['down']).model_dump()


# ---------------------------------------------------------------------------
# WebSocket broadcast - updates only, no identity, no tx ids.
# ---------------------------------------------------------------------------

active_sockets: set[fastapi.WebSocket] = set()
sockets_lock = threading.Lock()


async def broadcast(message: dict) -> None:
  dead = []
  with sockets_lock:
    sockets = list(active_sockets)
  for ws in sockets:
    try:
      await ws.send_json(message)
    except Exception:  # noqa
      dead.append(ws)
  if dead:
    with sockets_lock:
      for ws in dead:
        active_sockets.discard(ws)


@app.websocket('/ws')
async def ws_endpoint(websocket: fastapi.WebSocket) -> None:
  await websocket.accept()
  with sockets_lock:
    active_sockets.add(websocket)
  try:
    while True:
      await websocket.receive_text()
  except fastapi.WebSocketDisconnect:
    pass
  finally:
    with sockets_lock:
      active_sockets.discard(websocket)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


@app.get('/api/config', response_model=ConfigResponse)
def get_config() -> ConfigResponse:
  return ConfigResponse(max_note_length=MAX_NOTE_LENGTH)


@app.post('/api/session', response_model=BoardSummaryResponse)
def create_session(payload: BoardSessionRequest) -> BoardSummaryResponse:
  with lock:
    note_responses = [NoteSummaryResponse(**_note_summary(n)) for n in notes.values()]
    upvoted: list[str] = []
    downvoted: list[str] = []
    for transaction_id in payload.transaction_ids:
      transaction = votes.get(transaction_id)
      if transaction is None or transaction['superseded'] or transaction['value'] == 0:
        continue
      if transaction['value'] == 1:
        upvoted.append(transaction['note_id'])
      else:
        downvoted.append(transaction['note_id'])

  return BoardSummaryResponse(notes=note_responses, upvoted=upvoted, downvoted=downvoted)


@app.post('/api/notes', response_model=CreateNoteResponse)
async def create_note(payload: CreateNoteRequest) -> CreateNoteResponse:
  text_hash = hashlib.sha256(payload.text.encode('utf-8')).hexdigest()

  with lock:
    if text_hash in note_hashes:
      raise fastapi.HTTPException(status_code=409, detail='An identical note already exists')

    note_id = uuid.uuid4().hex
    note = {
      'id': note_id,
      'text': payload.text,
      'hash': text_hash,
      'up': 0,
      'down': 0,
      'ts': time.time(),
    }
    notes[note_id] = note
    note_hashes.add(text_hash)
    _save()
    message = {'event': 'note_created', 'note': _note_summary(note)}

  await broadcast(message)  # TODO: run this with an async worker instead of waiting here
  return CreateNoteResponse()


@app.post('/api/votes', response_model=VoteResponse)
async def cast_vote(payload: VoteRequest) -> VoteResponse:
  with lock:
    note = notes.get(payload.note_id)
    if note is None:
      raise fastapi.HTTPException(status_code=404, detail='note not found')

    if payload.replaces is not None:
      old = votes.get(payload.replaces)
      if old is None or old['superseded'] or old['note_id'] != payload.note_id:
        raise fastapi.HTTPException(status_code=400, detail='Replaces references an invalid transaction')
      old['superseded'] = True
      _apply_vote_delta(note, old['value'], -1)

    tx_id = uuid.uuid4().hex
    votes[tx_id] = {'note_id': payload.note_id, 'value': payload.value, 'superseded': False}
    _apply_vote_delta(note, payload.value, 1)
    _save()
    message = {'event': 'vote_updated', 'note_id': note['id'], 'up': note['up'], 'down': note['down']}

  await broadcast(message)
  return VoteResponse(tx_id=tx_id)


@app.get('/', summary='Static page response')
def index() -> fastapi.responses.FileResponse:
  return fastapi.responses.FileResponse(BASE_DIR / 'index.html')
