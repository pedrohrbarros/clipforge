from app.transcribe import Word, merge_transcripts, sentence_lines


def test_merge_transcripts_offsets_words_and_segments_by_chunk_start():
    parts = [
        (
            0.0,
            {
                "words": [{"word": "hi", "start": 0.0, "end": 0.5}],
                "segments": [{"text": "hi", "start": 0.0, "end": 0.5}],
            },
        ),
        (
            600.0,
            {
                "words": [{"word": "again", "start": 1.0, "end": 1.4}],
                "segments": [{"text": "again", "start": 1.0, "end": 1.4}],
            },
        ),
    ]
    words, segments = merge_transcripts(parts)
    assert [w.text for w in words] == ["hi", "again"]
    assert words[1].start == 601.0
    assert segments[1].end == 601.4


def test_sentence_lines_end_at_sentences_and_split_long_ones_at_commas():
    words = [
        Word("Hi", 0.0, 0.5),
        Word("there.", 0.5, 1.0),
        Word("This", 1.0, 3.0),
        Word("runs", 3.0, 6.0),
        Word("long,", 6.0, 9.0),
        Word("so", 9.0, 9.5),
        Word("it", 9.5, 10.0),
        Word("splits", 10.0, 10.5),
    ]
    assert [(s.text, s.start, s.end) for s in sentence_lines(words)] == [
        ("Hi there.", 0.0, 1.0),
        ("This runs long,", 1.0, 9.0),
        ("so it splits", 9.0, 10.5),
    ]
