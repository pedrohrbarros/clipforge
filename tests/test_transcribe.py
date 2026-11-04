from app.transcribe import merge_transcripts


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
