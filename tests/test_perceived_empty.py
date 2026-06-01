import pathlib
import unittest

from torque.perceived_empty import (
    PerceivedEmptyDetector,
    classify_result_payload,
    replay_transcript_for_episode,
    transcript_observations,
)


ATLAS_TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects"
A0B4DBD1 = (
    ATLAS_TRANSCRIPTS
    / "-Users-aleksanderarruda-dev-allierce-gh-atlaspublico--torque-worktrees-a0b4dbd1"
    / "75822b04-b9dd-4dd3-9ccc-5f5058091344.jsonl"
)
E18816EEA = (
    ATLAS_TRANSCRIPTS
    / "-Users-aleksanderarruda-dev-allierce-gh-atlaspublico--torque-worktrees-18816eea"
    / "81410cf4-7095-4957-ae38-ccc9c96fc221.jsonl"
)
AD9E1424 = (
    ATLAS_TRANSCRIPTS
    / "-Users-aleksanderarruda-dev-allierce-gh-atlaspublico--torque-worktrees-ad9e1424"
    / "c8f728d6-7c28-401b-8561-dcdec7cbbca3.jsonl"
)
CLEAN_4193D084 = (
    ATLAS_TRANSCRIPTS
    / "-Users-aleksanderarruda-dev-allierce-gh-atlaspublico--torque-worktrees-4193d084"
    / "faa5b498-6697-4ecf-af2a-fba8d102324e.jsonl"
)


def _require_transcript(path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        raise unittest.SkipTest(f"Atlas transcript fixture not present: {path}")
    return path


class PerceivedEmptyDetectorTests(unittest.TestCase):
    def test_tool_reference_blocks_are_non_empty_results(self):
        result_len, content_type = classify_result_payload([
            {"type": "tool_reference", "tool_name": "mcp__torque__torque_progress"},
            {"type": "tool_reference", "tool_name": "mcp__torque__torque_done"},
        ])

        self.assertGreater(result_len, 0)
        self.assertEqual(content_type, "tool_reference")

    def test_replays_a0b4dbd1_dead_channel_blocker_episode(self):
        path = _require_transcript(A0B4DBD1)

        episodes = replay_transcript_for_episode(
            path,
            cell_id="a0b4dbd1",
            group="Atlas",
            agent_name="search-fix-forward",
        )

        self.assertTrue(episodes)
        episode = episodes[-1]
        self.assertIn("dead-channel", episode.trigger_reason)
        self.assertEqual(episode.confidence, "high")
        self.assertEqual(episode.transcript_path, str(path))
        self.assertTrue(any(call["result_len"] > 0 for call in episode.tool_calls))

    def test_replays_18816eea_redundant_toolsearch_episode(self):
        path = _require_transcript(E18816EEA)

        episodes = replay_transcript_for_episode(
            path,
            cell_id="18816eea",
            group="Atlas",
            agent_name="entity-aware-search",
        )

        self.assertTrue(episodes)
        episode = episodes[-1]
        self.assertIn("toolsearch:torque-reporting", episode.trigger_reason)
        self.assertEqual(episode.confidence, "high")
        self.assertGreaterEqual(len(episode.tool_calls), 5)
        self.assertTrue(
            all(call["result_content_type"] == "tool_reference"
                for call in episode.tool_calls[-5:])
        )

    def test_clean_transcripts_do_not_fire(self):
        for cell_id, path in (
            ("ad9e1424", _require_transcript(AD9E1424)),
            ("4193d084", _require_transcript(CLEAN_4193D084)),
        ):
            with self.subTest(cell_id=cell_id):
                episodes = replay_transcript_for_episode(
                    path,
                    cell_id=cell_id,
                    group="Atlas",
                    agent_name="clean-worker",
                )
                self.assertEqual(episodes, [])

    def test_detector_suppresses_duplicate_episode_rows_inside_window(self):
        path = _require_transcript(E18816EEA)
        detector = PerceivedEmptyDetector()
        episodes = []
        for observation in transcript_observations(
            path,
            cell_id="18816eea",
            group="Atlas",
            agent_name="entity-aware-search",
        ):
            episode = detector.ingest_observation(observation)
            if episode is not None:
                episodes.append(episode)

        self.assertEqual(len(episodes), 1)


if __name__ == "__main__":
    unittest.main()
