"""The daemon and offline CLI agree on serializer-produced empty collections."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import unittest

from torque.actions import parse_yaml
from torque.server_actions import _action_to_yaml


class ActionYamlCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = SourceFileLoader('torque_cli_action_yaml', str(Path(__file__).resolve().parents[1] / 'bin' / 'torque'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.cli)

    def test_empty_collections_round_trip_in_daemon_and_offline_cli(self):
        values = {'prompt': '{{ TASK }}', 'agent': {}, 'labels': [],
                  'transitions': [], 'terminals': [],
                  'custom_metadata': {'empty_map': {}, 'empty_list': []}}
        serialized = _action_to_yaml('build', values)
        for parser in (parse_yaml, self.cli.parse_yaml):
            with self.subTest(parser=parser.__module__):
                loaded = parser(serialized)
                for key, value in values.items():
                    self.assertEqual(loaded[key], value, key)

    def test_quoted_collection_text_remains_text(self):
        for parser in (parse_yaml, self.cli.parse_yaml):
            with self.subTest(parser=parser.__module__):
                self.assertEqual(parser('empty_list: []\nempty_map: {}\nlist_text: "[]"\nmap_text: \'{}\'\n'),
                                 {'empty_list': [], 'empty_map': {}, 'list_text': '[]', 'map_text': '{}'})


    def test_serializer_quoted_prompt_keeps_unicode_spaces_and_escapes(self):
        import yaml
        prompt = ('{{ TASK }} — Olá 👋 ' + 'long text ' * 24 + '\n') * 3
        values = {'prompt': prompt, 'agent': {'command': 'echo "quoted"\nnext'},
                  'description': "author's note", 'transitions': [], 'terminals': []}
        serialized = _action_to_yaml('build', values)
        self.assertEqual(yaml.safe_load(serialized)['prompt'], prompt)
        for parser in (parse_yaml, self.cli.parse_yaml):
            with self.subTest(parser=parser.__module__):
                loaded = parser(serialized)
                for key, value in values.items():
                    self.assertEqual(loaded[key], value, key)

    def test_yaml_escaped_control_and_literal_backslashes_remain_distinct(self):
        import yaml
        values = {'escaped': '\0\a\b\t\n\v\f\r\x1b\x85\xa0\u2028\u2029',
                  'literal': r'\n C:\path\file', 'quoted': "it's 'quoted'"}
        serialized = yaml.safe_dump(values, allow_unicode=False, width=2**31 - 1)
        for parser in (parse_yaml, self.cli.parse_yaml):
            self.assertEqual(parser(serialized), values)
