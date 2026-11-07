"""Videos can be in any language. Each of these was a real way for one to go wrong without an error:
a line of Japanese counted as one word, normalised to nothing, or drawn off the side of the frame.

Run directly (no pytest dependency, no network):  python tests/test_languages.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kappacut as cf  # noqa: E402
import pipeline  # noqa: E402

JAPANESE = "昨日の夜に私たちは会社を辞めることを本気で考えていました"
CHINESE = "我们走进银行的时候他们根本没有看我们的计划"
THAI = "เมื่อวานนี้เราเกือบจะเลิกทำบริษัทนี้แล้วจริงๆ"
ARABIC = "دخلنا البنك ومعنا خطتنا كاملة"
HINDI = "हम अपनी पूरी योजना लेकर बैंक गए थे"
RUSSIAN = "Мы пришли в банк со всем нашим планом"
INDONESIAN = "Kami masuk ke bank dengan seluruh rencana kami"
SPANISH = "Entramos al banco con todo nuestro plan, señor"


# --- how many words

def test_text_with_spaces_counts_exactly_as_it_did():
    for text in ("So we walked into the bank with our whole plan.", INDONESIAN, SPANISH, ARABIC, HINDI, RUSSIAN, ""):
        assert cf.word_count(text) == len(text.split()), text


def test_a_line_without_spaces_isnt_one_word():
    assert cf.word_count(JAPANESE) >= 8 and cf.word_count(CHINESE) >= 8 and cf.word_count(THAI) >= 4


def test_nothing_is_no_words_and_one_character_is_one():
    assert cf.word_count(None) == 0 and cf.word_count("   ") == 0
    assert cf.word_count("好") == 1


def test_a_long_line_without_spaces_isnt_cut_short_as_a_held_sound():
    # five seconds and "one word" was treated like a stretched "ahhh", and lost its end
    line = {"text": JAPANESE, "start": 0.0, "end": 8.0, "avg_logprob": -0.2, "compression_ratio": 1.4, "no_speech_prob": 0.01}
    assert cf._segment_action(line, cf.DEFAULTS) is None


def test_a_short_stretched_line_still_is():
    line = {"text": "ああ", "start": 0.0, "end": 8.0, "avg_logprob": -0.2, "compression_ratio": 1.4, "no_speech_prob": 0.01}
    assert cf._segment_action(line, cf.DEFAULTS) is not None


def test_repeated_nonsense_is_still_dropped_in_any_script():
    line = {"text": "ああああああ", "start": 0.0, "end": 2.0, "avg_logprob": -0.2, "compression_ratio": 1.4, "no_speech_prob": 0.01}
    assert cf._segment_action(line, cf.DEFAULTS) == "drop:single-word-repeat"


def test_english_triage_is_unchanged():
    base = {"start": 0.0, "end": 2.0, "avg_logprob": -0.2, "compression_ratio": 1.4, "no_speech_prob": 0.01}
    assert cf._segment_action({**base, "text": "ARGH ARGH ARGH ARGH"}, cf.DEFAULTS) == "drop:single-word-repeat"
    assert cf._segment_action({**base, "text": "So we walked into the bank."}, cf.DEFAULTS) is None
    assert cf._segment_action({**base, "text": "ahhh", "end": 8.0}, cf.DEFAULTS) == "drop:held-noise"


# --- matching a line the model named

def test_a_line_keeps_its_letters_in_every_script():
    for text in (JAPANESE, CHINESE, ARABIC, HINDI, THAI, RUSSIAN):
        assert cf._norm_line(text), text          # a-z only left these empty


def test_different_lines_dont_count_as_the_same():
    # both used to come out empty, so the payoff was thrown away as a repeat of the hook
    assert cf._norm_line(JAPANESE) != cf._norm_line(CHINESE)
    assert cf._norm_line(ARABIC) != cf._norm_line(RUSSIAN)


def test_normalising_english_is_unchanged():
    assert cf._norm_line("  Don't STOP, it's 3 o'clock!_ ") == "dont stop its 3 oclock"


def test_a_named_line_is_found_in_any_script():
    segments = [{"start": 10.0, "end": 14.0, "text": JAPANESE}, {"start": 14.0, "end": 18.0, "text": CHINESE}]
    assert cf._match_line(CHINESE, segments, 12.0, 20.0)["start"] == 14.0
    assert cf._match_line(ARABIC, [{"start": 1.0, "end": 2.0, "text": ARABIC}], 0.0, 5.0)["start"] == 1.0


def test_a_few_characters_are_too_short_to_match_but_a_short_sentence_isnt():
    assert cf._match_line("好的", [{"start": 1.0, "end": 2.0, "text": "好的我们走吧"}], 0.0, 5.0) is None
    assert cf._match_line("我们走进银行", [{"start": 1.0, "end": 2.0, "text": CHINESE}], 0.0, 5.0) is not None
    assert cf._match_line("so we go", [{"start": 1.0, "end": 2.0, "text": "so we go home"}], 0.0, 5.0) is None    # as before


# --- where the topic changes

def test_vocabulary_is_seen_in_every_script():
    for text in (JAPANESE, CHINESE, ARABIC, HINDI, RUSSIAN, THAI):
        assert cf._content_words([{"text": text}]), text


def test_accented_words_stay_whole():
    assert "señor" in cf._content_words([{"text": SPANISH}])
    assert "नहीं" in cf._content_words([{"text": "मुझे नहीं पता"}])         # its marks are part of the word


def test_english_vocabulary_is_unchanged():
    words = cf._content_words([{"text": "So we walked into the bank, and they don't care about 2024 plans."}])
    assert {"walked", "into", "bank", "don't", "care", "plans"} <= words
    assert "about" not in words and "they" not in words and "2024" not in words and "and" not in words


def test_a_change_of_topic_shows_without_spaces_too():
    same = cf._content_words([{"text": "银行计划"}]) & cf._content_words([{"text": "银行的计划书"}])
    other = cf._content_words([{"text": "银行计划"}]) & cf._content_words([{"text": "今天天气很好"}])
    assert same and not other


# --- which language

def test_the_language_most_samples_agree_on_is_used():
    assert cf.dominant_language(["Spanish", "Spanish", "Spanish"], 3) == "es"
    assert cf.dominant_language(["Spanish", "Spanish", "English"], 3) == "es"
    assert cf.dominant_language(["indonesian", "indonesian"], 3) == "id"       # any casing
    assert cf.dominant_language(["Japanese"], 1) == "ja"


def test_an_english_video_is_transcribed_as_english_as_before():
    assert cf.dominant_language(["English", "English", "English"], 3) == "en"
    assert cf.dominant_language(["English", "english", "Spanish"], 3) == "en"      # a foreign clip inside


def test_not_sure_leaves_whisper_to_listen_for_itself():
    assert cf.dominant_language(["English", "Spanish"], 3) is None                 # a tie
    assert cf.dominant_language(["Spanish", "German", "English"], 3) is None       # no majority
    assert cf.dominant_language(["Spanish"], 3) is None                            # one sample in three had speech
    assert cf.dominant_language([], 3) is None                                     # music or silence
    assert cf.dominant_language(["Klingon", "Klingon"], 3) is None                 # no code to send


def test_whisper_can_answer_with_a_code_or_a_name():
    assert cf.language_code("Indonesian") == "id" and cf.language_code("id") == "id" and cf.language_code(" ZH ") == "zh"
    assert cf.language_code("Burmese") == "my" and cf.language_code("Haitian Creole") == "ht"
    assert cf.language_code("") is None and cf.language_code("not a language") is None


# --- captions

def _words(*texts, gap=0.3):
    return [{"w": t, "start": round(i * gap, 2), "end": round((i + 1) * gap, 2)} for i, t in enumerate(texts)]


def test_a_long_line_without_spaces_is_broken_to_fit_the_frame():
    # libass only wraps at spaces, so this ran straight off the side
    fitted = pipeline._fit_line(JAPANESE * 2, 62, 1080)
    assert "\\N" in fitted
    room = (1080 - 160) / 62
    assert all(len(line) <= room + 1 for line in fitted.split("\\N"))


def test_breaking_a_line_loses_nothing():
    for text in (JAPANESE * 3, THAI * 4, CHINESE + " " + CHINESE):
        assert pipeline._fit_line(text, 62, 1080).replace("\\N", " ").replace(" ", "") == text.replace(" ", "")


def test_a_line_with_spaces_is_left_to_libass():
    for text in ("So we walked into the bank with our whole plan and they all laughed at us.", ARABIC, HINDI, RUSSIAN):
        assert pipeline._fit_line(text, 62, 1080) == text


def test_no_line_starts_with_closing_punctuation_or_a_mark():
    fitted = pipeline._fit_line("我们走进银行，" * 6 + "他们根本没有看。" * 6, 62, 1080)
    for line in fitted.split("\\N"):
        assert line[0] not in "，。！？）"
    for line in pipeline._fit_line(THAI * 6, 62, 1080).split("\\N"):
        assert not cf.unicodedata.category(line[0]).startswith("M")


def test_a_wider_frame_fits_more():
    assert pipeline._fit_line(JAPANESE * 2, 62, 1920).count("\\N") < pipeline._fit_line(JAPANESE * 2, 62, 1080).count("\\N")


def test_karaoke_groups_hold_english_as_before():
    groups = pipeline._chunk_words(_words("one", "two", "three", "four", "five", "six", "seven"))
    assert [len(g) for g in groups] == [5, 2]


def test_karaoke_groups_in_a_script_without_spaces_go_by_characters():
    groups = pipeline._chunk_words(_words(*"我们走进银行的时候他们根本没有看我们的计划", gap=0.1))
    assert all(sum(len(w["w"]) for w in g) <= pipeline._KAR_MAX_CHARS + 1 for g in groups)
    assert len(groups) >= 2


def test_karaoke_stops_at_any_scripts_full_stop():
    groups = pipeline._chunk_words(_words("好的。", "我们", "走吧。", "现在", gap=0.1))
    assert groups[0][-1]["w"] == "好的。"


def _ass_body(lines, **kw):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = pipeline.build_ass(lines, "bold", "bottom", 1.0, True, Path(tmp) / "t.ass", **kw)
        return path.read_text(encoding="utf-8")


def test_karaoke_puts_no_spaces_between_characters():
    body = _ass_body([{"start": 0, "end": 2, "text": "", "words": _words("我们", "走进", "银行")}], karaoke=True)
    assert "走进银行" in body and "走进 银行" not in body


def test_karaoke_keeps_spaces_between_words():
    body = _ass_body([{"start": 0, "end": 2, "text": "", "words": _words("We", "went", "home")}], karaoke=True)
    assert "went home" in body


def test_captions_are_written_as_they_are():
    body = _ass_body([{"start": 0, "end": 2, "text": ARABIC}, {"start": 2, "end": 4, "text": RUSSIAN}, {"start": 4, "end": 6, "text": HINDI}])
    assert ARABIC in body and RUSSIAN in body and HINDI in body


def test_a_title_in_any_script_still_names_a_file():
    # bucket keys are ASCII: the clip number keeps these apart
    assert cf.slugify(JAPANESE) == "clip" and cf.slugify(ARABIC) == "clip"
    assert cf.slugify("El señor del banco") == "el-senor-del-banco"
    assert pipeline.clip_filename({"label": JAPANESE}, 3) == "03_clip.mp4"


# --- what the model is told

def test_the_picker_and_judge_are_told_any_language():
    assert "any language" in cf.PICKER_BRIEF and "never translated" in cf.PICKER_BRIEF


def test_they_are_told_the_videos_language_by_name():
    # the general rule wasn't enough: titles for an Indonesian video still came back in English
    assert "This video is in Indonesian" in cf._steer_line({"language": "id"})
    assert "This video is in Spanish" in cf._steer_line({"language": "es"})      # not "Castilian"
    assert "This video is in Burmese" in cf._steer_line({"language": "my"})      # not "Myanmar"


def test_nothing_extra_is_said_for_english_or_when_not_sure():
    assert cf._steer_line({"language": "en"}) == "" and cf._steer_line({"language": None}) == "" and cf._steer_line({}) == ""


def test_every_language_has_a_name():
    assert set(cf.LANGUAGE_NAMES) == set(cf.WHISPER_LANGUAGES.values())


def main() -> int:
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:200]}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
