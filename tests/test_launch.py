"""The launch rules that the database and browser don't check: who may sign up, what a person
is told when a job fails, how a job gets stopped, and which language counts as "not English".

Same convention as the other tests: plain functions, bare asserts, runs as
`python tests/test_launch.py` or under pytest. No database, no bucket, no AI calls.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for k, v in {"S3_ENDPOINT_URL": "http://minio:9000", "S3_BUCKET": "kappacut-test",
             "S3_ACCESS_KEY_ID": "testkey", "S3_SECRET_ACCESS_KEY": "testsecret"}.items():
    os.environ.setdefault(k, v)

from fastapi import HTTPException  # noqa: E402

import kappacut as cf  # noqa: E402
from app import email as email_mod  # noqa: E402
from app import jobs, storage  # noqa: E402
from app.auth.manager import UserManager  # noqa: E402
from app.auth.schemas import UserCreate  # noqa: E402
from app.config import Settings, TERMS_VERSION, settings  # noqa: E402
from app.launch import processors  # noqa: E402

FFMPEG_ERROR = ("command failed: ffmpeg -y -i <url> ...\n"
                "[mov,mp4 @ 0x55] moov atom not found\n/tmp/kappacut_render_x/source.mp4: Invalid data")


# --- which language counts as "not English"

def test_samples_agreeing_on_another_language_are_named():
    assert cf.foreign_language(["Spanish", "Spanish", "Spanish"], 3) == "Spanish"
    assert cf.foreign_language(["Spanish", "Spanish", "English"], 3) == "Spanish"
    assert cf.foreign_language(["indonesian", "indonesian"], 3) == "Indonesian"     # any casing


def test_a_short_video_can_be_judged_on_its_one_sample():
    assert cf.foreign_language(["German"], 1) == "German"
    assert cf.foreign_language([], 1) is None


def test_english_and_uncertainty_never_stop_a_video():
    assert cf.foreign_language(["English", "English", "English"], 3) is None
    assert cf.foreign_language(["English", "english", "Spanish"], 3) is None        # a foreign clip inside
    assert cf.foreign_language(["English", "Spanish"], 3) is None                   # a tie
    assert cf.foreign_language(["Spanish"], 3) is None                              # one sample in three had speech
    assert cf.foreign_language([], 3) is None                                       # music or silence
    assert cf.foreign_language(["en", "en"], 3) is None                             # a code, not a name
    assert cf.foreign_language(["id", "id", "id"], 3) is None                       # not worth a confusing message


# --- a free user's minutes

def test_minutes_used_can_be_added_to_a_videos_length():
    """Postgres hands back SUM(bigint) as a Decimal. Adding that to a float raises, which failed
    every analysis after a free user's first, and the API sent the number as a string."""
    from decimal import Decimal

    from app.quota import monthly_minutes

    async def scalar(_query):
        return Decimal("90")

    used = asyncio.run(monthly_minutes(SimpleNamespace(scalar=scalar), uuid.uuid4()))
    assert type(used) is float and used == 1.5
    assert used + 2.5 == 4.0


def test_a_video_longer_than_the_minutes_left_is_refused_with_the_numbers():
    from decimal import Decimal

    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, quota_minutes_override=None)

    class Session(_Db):
        async def scalar(self, _query):
            return Decimal(58 * 60)                 # 58 of the 60 free minutes used

        async def get(self, _model, _id):
            return user

    async def run(seconds):
        async def in_thread(_scope, _fn, *_args):
            return seconds
        real = jobs.async_session_maker, jobs._in_thread, jobs.storage.presign_internal
        jobs.async_session_maker, jobs._in_thread = Session([]), in_thread
        jobs.storage.presign_internal = lambda *_a, **_k: "url"
        try:
            return await jobs._minutes_short(user.id, "key")
        finally:
            jobs.async_session_maker, jobs._in_thread, jobs.storage.presign_internal = real

    assert asyncio.run(run(60)) is None                         # one minute fits in the two left
    msg = asyncio.run(run(600))
    assert "10 min long" in msg and "2 of your 60" in msg, msg


# --- what a person is told when a job fails

def _quota_error():
    return type("GroqQuotaError", (RuntimeError,), {})("Groq's daily limit is used up. Try again in ~12 min.")


def test_ffmpeg_output_never_reaches_the_user():
    for kind in ("analyze", "clip"):
        msg = jobs.explain(RuntimeError(FFMPEG_ERROR), kind)
        for leak in ("ffmpeg", "moov", "/tmp", "RuntimeError", "command failed", "Invalid data"):
            assert leak not in msg, msg
    assert "MP4" in jobs.explain(RuntimeError(FFMPEG_ERROR), "analyze")
    assert "clip" in jobs.explain(RuntimeError(FFMPEG_ERROR), "clip")


def test_unknown_errors_are_hidden_and_give_a_next_step():
    msg = jobs.explain(KeyError("segments[3].words"), "analyze")
    assert "segments" not in msg and "KeyError" not in msg and "try again" in msg


def test_failures_a_retry_can_fix_say_so():
    assert "few minutes" in jobs.explain(type("APITimeoutError", (Exception,), {})("x"), "analyze")


def test_a_message_already_written_for_people_stays():
    assert jobs.explain(_quota_error(), "analyze") == "Groq's daily limit is used up. Try again in ~12 min."


def test_the_language_message_says_what_happened_to_their_minutes():
    msg = jobs.not_english_msg("Spanish")
    assert "Spanish" in msg and "English" in msg and "minutes" in msg


# --- stopping a job

class _Db:
    """Stands in for async_session_maker: every session reads the next job status."""

    def __init__(self, statuses):
        self.statuses = iter(statuses)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def scalar(self, _query):
        value = next(self.statuses)
        if isinstance(value, Exception):
            raise value
        return value


def _watch(statuses, then, stop_after=1, polls=60):
    """Runs _watch_job over `statuses`, then `then` forever; returns the scopes it stopped.
    Ends once it has stopped `stop_after` times, or after `polls` polls with nothing."""
    stopped = []
    real = (jobs.async_session_maker, jobs.JOB_WATCH_SECONDS, cf.cancel_scope)
    jobs.async_session_maker, jobs.JOB_WATCH_SECONDS = _Db(list(statuses) + [then] * 5000), 0.001
    cf.cancel_scope = stopped.append

    async def run():
        task = asyncio.create_task(jobs._watch_job(uuid.uuid4(), "scope-x"))
        for _ in range(polls):
            await asyncio.sleep(0.001)
            if len(stopped) >= stop_after:
                break
        task.cancel()

    try:
        asyncio.run(run())
    finally:
        jobs.async_session_maker, jobs.JOB_WATCH_SECONDS, cf.cancel_scope = real
    return stopped


def test_a_running_job_is_left_alone():
    assert _watch(["queued", "running"], then="running") == []


def test_an_ended_or_deleted_job_stops_its_work():
    assert _watch(["running"], then="error") == ["scope-x"]
    assert _watch([], then="done") == ["scope-x"]
    assert _watch([], then=None) == ["scope-x"]         # the row is gone: a deleted account or project


def test_the_watch_keeps_stopping_in_case_the_work_hadnt_started():
    assert len(_watch([], then="error", stop_after=3)) == 3


def test_a_database_blip_isnt_a_cancel():
    assert _watch([OSError("db down")], then="running") == []
    assert _watch([OSError("db down")], then="error") == ["scope-x"]    # and it still notices afterwards


def test_cancelling_a_scope_kills_a_running_process_and_blocks_new_ones():
    """The real thing, on a real process: this is what an admin's Cancel ends up doing."""
    import threading

    started, result = threading.Event(), {}

    def work():
        with cf.proc_scope("kill-test"):
            started.set()
            try:
                cf.run([sys.executable, "-c", "import time; time.sleep(30)"])
            except cf.Cancelled as e:
                result["raised"] = e
            try:
                cf.run([sys.executable, "-c", "print('should never start')"])
            except cf.Cancelled as e:
                result["second"] = e

    t = threading.Thread(target=work)
    t.start()
    started.wait(5)
    while not cf._PROCS.get("kill-test"):           # the process is registered just after the event
        pass
    cf.cancel_scope("kill-test")
    t.join(10)
    assert not t.is_alive() and "raised" in result and "second" in result


# --- signing up

class _UserDb:
    def __init__(self, invited=False):
        self.invited, self.updates, self.created = invited, [], []
        self.session = SimpleNamespace(scalar=self._scalar)

    async def _scalar(self, _query):
        return uuid.uuid4() if self.invited else None

    async def get_by_email(self, _email):
        return None

    async def create(self, values):
        self.created.append(values)
        return SimpleNamespace(id=uuid.uuid4(), is_verified=True, email=values["email"], **{})

    async def update(self, user, values):
        self.updates.append(values)
        return user


def _signup(body, invite_only=False, invited=False, safe=True):
    db, real = _UserDb(invited), settings.invite_only
    settings.invite_only = invite_only
    try:
        user = asyncio.run(UserManager(db).create(UserCreate(**body), safe=safe))
    finally:
        settings.invite_only = real
    return user, db


_ME = {"email": "new@example.com", "password": "long-enough-1"}


def _refused_with(body, **kw) -> int:
    try:
        _signup(body, **kw)
    except HTTPException as e:
        return e.status_code
    raise AssertionError("accepted")


def test_signing_up_needs_the_terms_ticked():
    assert _refused_with(_ME) == 400
    assert _refused_with({**_ME, "accept_terms": False}) == 400


def test_signing_up_records_which_terms_were_accepted():
    _, db = _signup({**_ME, "accept_terms": True})
    assert db.updates[0]["terms_version"] == TERMS_VERSION and db.updates[0]["terms_accepted_at"]


def test_the_tick_box_never_reaches_the_users_table():
    _, db = _signup({**_ME, "accept_terms": True})
    assert "accept_terms" not in db.created[0] and "password" not in db.created[0]


def test_invite_only_lets_in_only_invited_emails():
    assert _refused_with({**_ME, "accept_terms": True}, invite_only=True, invited=False) == 403
    _signup({**_ME, "accept_terms": True}, invite_only=True, invited=True)
    _signup({**_ME, "accept_terms": True}, invite_only=False, invited=False)


def test_the_admin_cli_can_make_the_first_admin_with_no_invite():
    su = {**_ME, "is_superuser": True, "is_verified": True}
    _, db = _signup(su, invite_only=True, invited=False, safe=False)
    assert db.created[0]["is_superuser"] is True and db.updates == []


# --- a public deploy needs someone named on its legal pages

def _public(**over) -> Settings:
    good = {"environment": "prod", "cookie_secure": True, "jwt_secret": "a" * 40,
            "verification_token_secret": "b" * 40, "reset_password_token_secret": "c" * 40,
            "s3_bucket": "b", "s3_endpoint_url": "https://x.r2.cloudflarestorage.com",
            "s3_access_key_id": "k", "s3_secret_access_key": "s", "smtp_host": "smtp.resend.com",
            "smtp_from": "Kappacut <no-reply@example.com>", "groq_api_key": "g",
            "support_email": "help@example.com", "legal_name": "A Person",
            "legal_address": "1 Street, Town", "legal_jurisdiction": "Germany"}
    return Settings(_env_file=None, **{**good, **over})


def test_a_public_deploy_refuses_to_boot_without_legal_details():
    _public().check_production_ready()
    for field in ("support_email", "legal_name", "legal_address", "legal_jurisdiction"):
        try:
            _public(**{field: "   "}).check_production_ready()
        except RuntimeError as e:
            assert field.upper() in str(e)
        else:
            raise AssertionError(f"booted with no {field}")


# --- the Privacy Policy names who actually gets data

def _names(cfg) -> str:
    return " | ".join(p["name"] for p in processors(cfg))


def test_groq_is_always_named_and_claude_only_when_it_gets_data():
    assert "Groq" in _names({"backend": "groq", "vision": False})
    assert "Anthropic" not in _names({"backend": "groq", "vision": False})
    claude = next(p for p in processors({"backend": "claude", "vision": False}) if "Anthropic" in p["name"])
    assert "transcript" in claude["gets"] and "frame" not in claude["gets"]
    vision = next(p for p in processors({"backend": "groq", "vision": True}) if "Anthropic" in p["name"])
    assert "frame" in vision["gets"] and "transcript" not in vision["gets"]


def test_the_real_storage_and_email_providers_are_named():
    real = settings.s3_endpoint_url, settings.smtp_host
    try:
        settings.s3_endpoint_url, settings.smtp_host = "https://abc.r2.cloudflarestorage.com", "smtp.resend.com"
        names = _names({})
        assert "Cloudflare R2" in names and "Resend" in names
        settings.s3_endpoint_url, settings.smtp_host = "https://s3.example.com", "mail.example.com"
        names = _names({})                              # never a wrong name, if the setup differs
        assert "Cloudflare" not in names and "Resend" not in names and "Our storage provider" in names
    finally:
        settings.s3_endpoint_url, settings.smtp_host = real


def test_thumbnail_providers_appear_only_with_their_keys():
    real = settings.together_api_key, settings.replicate_api_key
    try:
        settings.together_api_key, settings.replicate_api_key = "", ""
        assert "Together" not in _names({}) and "Replicate" not in _names({})
        settings.together_api_key = "k"
        assert "Together" in _names({}) and "Replicate" not in _names({})
    finally:
        settings.together_api_key, settings.replicate_api_key = real


# --- exporting data

def test_an_export_has_real_files_and_leaves_out_rebuildable_images():
    uid = uuid.uuid4()
    prefix = f"users/{uid}/projects/p1/"
    objects = {f"users/{uid}/": [{"Key": prefix + n, "Size": 5} for n in
                                 ("source.mp4", "clips/a.mp4", "frames/10.jpg", "waveform.png", "thumbnails/0.png")],
               f"sfx/users/{uid}/": [{"Key": f"sfx/users/{uid}/boom.m4a", "Size": 7}]}
    real = storage.list_prefix, storage.presign_get
    storage.list_prefix = lambda p: objects[p]
    storage.presign_get = lambda key, ttl=None: f"signed:{key}:{ttl}"
    try:
        out = storage.export_links(uid, 3600)
    finally:
        storage.list_prefix, storage.presign_get = real
    names = {f["file"] for f in out}
    assert names == {"projects/p1/source.mp4", "projects/p1/clips/a.mp4", "projects/p1/thumbnails/0.png", "boom.m4a"}
    assert all(f["url"].endswith(":3600") and f["bytes"] for f in out)


# --- emails

def _sent_by(send, *args):
    sent = []

    async def capture(to, subject, body, **kw):
        sent.append((body, kw))

    real = email_mod.send_email
    email_mod.send_email = capture
    try:
        asyncio.run(send(*args))
    finally:
        email_mod.send_email = real
    return sent[0]


def test_a_project_name_cant_put_markup_in_a_takedown_email():
    body, _ = _sent_by(email_mod.send_takedown_notice, "a@b.c", "<img src=x onerror=1>", "<script>ref</script>")
    assert "<img" not in body and "<script" not in body and "&lt;img" in body


def test_mail_about_a_removal_or_deletion_doesnt_say_you_can_ignore_it():
    assert _sent_by(email_mod.send_takedown_notice, "a@b.c", "x", "y")[1] == {"ignorable": False}
    assert _sent_by(email_mod.send_retention_warning, "a@b.c", "x", 3)[1] == {"ignorable": False}
    assert _sent_by(email_mod.send_invite_email, "a@b.c")[1] == {}          # this one, you can ignore


def test_every_email_can_be_built_with_or_without_the_footer():
    assert "ignore this email" in email_mod._SHELL.format(body="b", footer=email_mod._IGNORE_ME)
    assert "ignore" not in email_mod._SHELL.format(body="b", footer="")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
