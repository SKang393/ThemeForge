"""Bound checks use a short owned sleeper, never an encoder or LLM."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from themeforge import neighbor_runner as runner
from tests.test_semantic_neighbors import fixture


class NeighborRunnerTests(unittest.TestCase):
    def inputs(self, directory):
        packet, profile = Path(directory) / 'packet.json', Path(directory) / 'profile.json'
        packet.write_text(json.dumps(fixture()), encoding='utf-8')
        profile.write_text('{}', encoding='utf-8')
        return packet, runner.file_sha256(packet), profile, runner.file_sha256(profile)

    def test_oversized_profile_rejected_before_hash_or_process(self):
        with tempfile.TemporaryDirectory() as directory:
            packet, packet_pin, profile, profile_pin = self.inputs(directory)
            profile.write_bytes(b' ' * (128 * 1024 + 1))
            with patch.object(runner, 'file_sha256', wraps=runner.file_sha256) as hashing, patch.object(subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    runner.run_comparison(packet, packet_pin, profile, profile_pin, Path(directory) / 'out')
                self.assertNotIn(profile, [call.args[0] for call in hashing.call_args_list])
                launch.assert_not_called()

    def test_elapsed_guard_stops_only_owned_helper_and_preserves_failure_record(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = self.inputs(directory)
            original = subprocess.Popen
            launched = []
            def sleeper(command, **kwargs):
                # Preserve the real hidden subprocess/supervision path, without inference.
                process = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
                launched.append(process)
                return process
            with patch.object(subprocess, 'Popen', side_effect=sleeper), patch.object(runner, 'WALL_LIMIT_SECONDS', .15):
                report = runner.run_comparison(*arguments, Path(directory) / 'out')
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(len(launched), 1)
            self.assertIsNotNone(launched[0].poll())
            self.assertEqual(report['arms'][0]['stopped_by_guard'], 'wall_time')
            self.assertTrue(report['owned_processes_closed'])
            self.assertTrue((Path(directory) / 'out' / 'comparison-runtime.json').exists())
            self.assertFalse((Path(directory) / 'out' / 'sentence_embeddings').exists())


if __name__ == '__main__':
    unittest.main()
