"""Synthetic pixel arithmetic only."""
import numpy as np
import pytest
from wb.quantification import validate
from wb.model import QuantificationGroup


def test_bounds_and_overlap():
    g = QuantificationGroup(group_id='q',panel_id='p',capture_id='c',source_asset_id='s',frame_id='f',width_px=2,height_px=2,lanes=[dict(lane_id=1,x=0,y=0,background_x=0,background_y=2)])
    validate(np.zeros((4,4),dtype=np.uint16),g)
    g.lanes[0].background_y=1
    with pytest.raises(ValueError,match='overlaps'):
        validate(np.zeros((4,4),dtype=np.uint16),g)
    g.lanes[0].background_y=4
    with pytest.raises(ValueError,match='outside'):
        validate(np.zeros((4,4),dtype=np.uint16),g)


@pytest.mark.parametrize('polarity,band,bg,expected', [
    ('high', 500, 100, 1600), ('high', 50, 100, -200),
    ('low', 100, 500, 1600), ('low', 500, 100, -1600),
])
def test_signal_arithmetic(monkeypatch, polarity, band, bg, expected):
    from wb import quantification as q
    g = QuantificationGroup(group_id='q', panel_id='p', capture_id='c', source_asset_id='s', frame_id='f',
        width_px=2, height_px=2, signal_polarity=polarity, source_confirmed=True,
        lanes=[dict(lane_id=1,x=0,y=0,background_x=0,background_y=2)])
    a = np.full((4, 4), bg, dtype=np.uint16)
    a[:2,:2] = band
    monkeypatch.setattr(q, 'load', lambda *args: a)
    monkeypatch.setattr(q, 'identity', lambda *args: ['Sample 1'])
    monkeypatch.setattr(q, 'fingerprint', lambda *args: 'synthetic')
    q.compute(None, None, g)
    assert g.results['rows'][0]['net_signal'] == expected
    assert g.results['rows'][0]['area_px'] == 4


@pytest.mark.parametrize('polarity,value', [('high',65535), ('low',0)])
def test_saturation_follows_signal_direction(monkeypatch, polarity, value):
    from wb import quantification as q
    g = QuantificationGroup(group_id='q', panel_id='p', capture_id='c', source_asset_id='s', frame_id='f',
        width_px=2, height_px=2, signal_polarity=polarity, source_confirmed=True,
        lanes=[dict(lane_id=1,x=0,y=0,background_x=0,background_y=2)])
    a = np.full((4,4), 100, dtype=np.uint16)
    a[0,0] = value
    monkeypatch.setattr(q, 'load', lambda *args: a)
    monkeypatch.setattr(q, 'identity', lambda *args: ['Sample 1'])
    monkeypatch.setattr(q, 'fingerprint', lambda *args: 'synthetic')
    q.compute(None,None,g)
    row = g.results['rows'][0]
    assert row['status'] == 'saturated'
    assert row['net_signal'] is None
    assert row['saturated_pixels'] == 1
