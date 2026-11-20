import json
from pathlib import Path
import pytest
from linkread_desktop.links import canonical_link, pasted_url
from linkread_desktop.pipeline import video_source_key
from linkread_desktop.desktop import _video_url
from linkread_desktop.metadata import canonical_video_url
from linkread_desktop.errors import UnsupportedSourceError

CASES = json.loads((Path(__file__).parent/'fixtures/video_links.json').read_text())

@pytest.mark.parametrize('case', CASES)
def test_shared_link_fixtures(case):
    if case['output'] is None:
        with pytest.raises(UnsupportedSourceError): canonical_link(case['input'])
    else:
        assert canonical_link(case['input']) == case['output']
        assert _video_url(case['input']) == case['output']
        if 'b23.tv' not in case['output']:
            assert canonical_video_url(case['input']) == case['output']
            assert video_source_key(case['input']) == video_source_key(case['output'])


def test_local_file_names_not_unescaped():
    assert pasted_url(r'/tmp/my\ file.srt') == r'/tmp/my\ file.srt'
