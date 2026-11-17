#!/usr/bin/env python3
"""Today's focus: pick the few names worth watching next session, with one level to break and one to hold.

After the close (after run.py):   python3 scripts/focus.py pick
Before the open (pre-market):      python3 scripts/focus.py pre --prices SPY=765.1,QQQ=710.2,...
The thread's card in plain words:  python3 scripts/focus.py card [--session YYYY-MM-DD]

Writes options-tab/data/focus/<session>.json. push_options.py --focus adds it to the edition.

How names are picked (all from the levels file and that day's iv_history row; nothing is predicted):
  - nearest resistance zone above the close and nearest support zone below it, in ATRs;
  - the closer the price sits to one of them, the more likely a decision comes early in the session;
  - option volume against its average (activity), capped at 2x;
  - range: ATR as a share of price, so a name can pay for an option;
  - SPY always takes a slot: it is the name traded most.
Bias: within 1.2 ATR of resistance with a close that was not WEAK -> breakout; nearer support -> support;
otherwise range (wait for a level).
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('/mnt/project-files/options-tab/data')
ALWAYS = 'SPY'
N = 4


SKIP = {'PDC', '52WL'}


def ladder(t):
    """Named levels, nearby ones merged (within 0.15 ATR), lowest first: [(price, 'PDH/PMH'), ...]."""
    a = t.get('atr14') or 0
    pts = sorted((l['price'], l['name']) for l in t.get('levels') or [] if l['name'] not in SKIP and l['name'] not in ('DEMAND', 'SUPPLY'))
    for l in t.get('levels') or []:
        if l['name'] == 'DEMAND' and l.get('hi'):
            pts.append((l['hi'], 'DEMAND'))
        if l['name'] == 'SUPPLY' and l.get('lo'):
            pts.append((l['lo'], 'SUPPLY'))
    pts.sort()
    out = []
    for p, n in pts:
        if out and p - out[-1][0] <= 0.15 * a:
            out[-1] = (out[-1][0], out[-1][1] + '/' + n)
        else:
            out.append((p, n))
    return out


def lvl(price, n):
    return {'price': round(price, 2), 'name': n[:40]}


def pick(session=None, data=DATA):
    files = sorted((data / 'levels').glob('*.json'))
    if session:
        files = [f for f in files if f.stem == session]
    lv = json.load(open(files[-1]))
    built = lv.get('built_from')
    iv = {r['ticker']: r for r in csv.DictReader(open(data / 'iv_history.csv')) if r['session'] == built}
    rows = []
    for sym, t in lv['tickers'].items():
        c, a = t['last']['close'], t.get('atr14') or 0
        if not a:
            continue
        lad = ladder(t)
        up = [x for x in lad if x[0] > c + 0.1 * a]
        dn = [x for x in lad if x[0] < c - 0.1 * a][::-1]
        brk, hold = (up[0][0] if up else None), (dn[0][0] if dn else None)
        d_r = (brk - c) / a if brk else 9
        d_s = (c - hold) / a if hold else 9
        x = iv.get(sym) or {}
        try:
            vr = (float(x['call_vol']) + float(x['put_vol'])) / (float(x['avg_call_vol']) + float(x['avg_put_vol']))
        except (KeyError, ValueError, ZeroDivisionError):
            vr = 1.0
        atr_pct = a / c
        near = min(d_r, d_s)
        score = 2.0 * max(0.0, 1.5 - near) + min(2.0, vr) + min(1.5, atr_pct * 50)
        chip = (t.get('close_strength') or {}).get('chip')
        if d_r <= 1.2 and d_r <= d_s and chip != 'WEAK':
            bias = 'breakout'
        elif d_s <= 1.2:
            bias = 'support'
        else:
            bias = 'range'
        nxt_up = next((x for x in up[1:] if x[0] - brk >= 0.5 * a), None) if up else None
        nxt_dn = next((x for x in dn[1:] if hold - x[0] >= 0.5 * a), None) if dn else None
        side = f'{d_r:.1f} ATR under resistance' if d_r <= d_s else f'{d_s:.1f} ATR above support'
        why = f'Closed {side}; close {(chip or "n/a").lower()}; option volume {vr:.1f}x normal.'
        rows.append({'symbol': sym, 'bias': bias, 'why': why, 'close': round(c, 2), 'atr': round(a, 2),
                     'resistance': lvl(*up[0]) if up else None, 'support': lvl(*dn[0]) if dn else None,
                     'breakout_above': round(brk, 2) if brk else None, 'hold_above': round(hold, 2) if hold else None,
                     'target_up': lvl(*nxt_up) if nxt_up else None,
                     'target_down': lvl(*nxt_dn) if nxt_dn else None,
                     'score': round(score, 3)})
    rows.sort(key=lambda r: -r['score'])
    top = [r for r in rows if r['symbol'] == ALWAYS][:1]
    top += [r for r in rows if r['symbol'] != ALWAYS][:N - len(top)]
    top.sort(key=lambda r: -r['score'])
    for i, r in enumerate(top, 1):
        r['rank'] = i
    doc = {'session': lv['session'], 'built_from': built,
           'picked_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           'method': 'Nearest level in ATRs, option activity and range, from the close; SPY always included.',
           'items': top, 'premarket': None}
    out = data / 'focus' / f"{lv['session']}.json"
    out.parent.mkdir(exist_ok=True)
    json.dump(doc, open(out, 'w'), indent=1)
    return out, doc


def where(p, it, a):
    b, h = it.get('breakout_above'), it.get('hold_above')
    if b and p > b:
        return 'above_resistance'
    if h and p < h:
        return 'below_support'
    if b and (b - p) <= 0.3 * a:
        return 'near_resistance'
    if h and (p - h) <= 0.3 * a:
        return 'near_support'
    return 'between'


NOTE = {
    'above_resistance': 'Gapping over the level: wait for a retest that holds before any call.',
    'below_support': 'Gapping under support: no call; a put only after a failed retest from below.',
    'near_resistance': 'Right under the level: let it break and retest first.',
    'near_support': 'Sitting on support: a call only if it holds and bounces; a break is a warning.',
    'between': 'Between the levels: no edge in the middle, wait for price to reach one.',
}


def pre(prices, session=None, data=DATA):
    files = sorted((data / 'focus').glob('*.json'))
    if session:
        files = [f for f in files if f.stem == session]
    path = files[-1]
    doc = json.load(open(path))
    items = []
    for it in doc['items']:
        p = prices.get(it['symbol'])
        if p is None:
            continue
        w = where(p, it, it.get('atr') or 0)
        items.append({'symbol': it['symbol'], 'price': round(p, 2),
                      'gap_pct': round((p / it['close'] - 1) * 100, 2) if it.get('close') else None,
                      'where': w, 'note': NOTE[w]})
    doc['premarket'] = {'as_of': datetime.now(timezone.utc).replace(microsecond=0).isoformat(), 'items': items}
    json.dump(doc, open(path, 'w'), indent=1)
    return path, doc


# The card in plain words. The same arithmetic as the dashboard (dashboard/src/lib/focus-plan.ts); keep them in step.
WORDS = {'PDH': "yesterday's high", 'PDL': "yesterday's low", 'PDC': "yesterday's close", 'PWH': "last week's high",
         'PWL': "last week's low", 'PMH': "last month's high", 'PML': "last month's low",
         'WTDH': "this week's high so far", 'WTDL': "this week's low so far", 'MA20': '20-day average price',
         'MA50': '50-day average price', 'MA200': '200-day average price', '52WH': 'one-year high',
         '52WL': 'one-year low', 'DEMAND': 'buying zone', 'SUPPLY': 'selling zone'}
ENTRY_ATR, STOP_ATR, ROOM = 0.01, 0.1, 2
BIAS_WORD = {'breakout': 'Breakout watch', 'support': 'Hold support', 'range': 'Range: wait'}
# Names whose options expire Mon/Wed/Fri only (IBKR, 27 Oct 2026); premiums near the money usually run over $2.
MWF_ONLY = {'AVGO', 'AMD'}


def words(name):
    return ', '.join(WORDS.get(c.strip(), c.strip()) for c in (name or '').split('/') if c.strip())


def side_plan(kind, level, atr, nxt):
    """Entry just through the level, stop 0.1 ATR back, first target at 2x the risk, then the next named level."""
    if level is None:
        return None
    d = 1 if kind == 'call' else -1
    entry = stop = t1 = None
    if atr:
        entry, stop = round(level + d * ENTRY_ATR * atr, 2), round(level - d * STOP_ATR * atr, 2)
        t1 = round(entry + d * ROOM * abs(entry - stop), 2)
    t2 = nxt if nxt and (t1 is None or d * (nxt['price'] - t1) > 0) else None
    return {'level': level, 'entry': entry, 'stop': stop, 't1': t1, 't2': t2}


def lv(price, name):
    w = words(name)
    return f'{price:.2f}' + (f' ({w})' if w else '')


def side_line(kind, p):
    over, back, retest = ('above', 'below', 'a dip holds it') if kind == 'call' else ('below', 'above', 'a bounce fails under it')
    out = f"{kind.capitalize()}: a 2-min candle closes {over} {p['level']:.2f}, then {retest}."
    if p['entry'] is not None:
        out += f" Enter with a limit about {p['entry']:.2f}. Stop on a 2-min close {back} {p['stop']:.2f} or -25%."
    else:
        out += f' Stop on a 2-min close back {back} the level or -25%.'
    tg = [f"{p['t1']:.2f}"] if p['t1'] is not None else []
    if p['t2']:
        tg.append(lv(p['t2']['price'], p['t2'].get('name')))
    if tg:
        out += ' Targets ' + ', then '.join(tg) + '.'
    return out


def sydney_times(session):
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    y, m, d = map(int, session.split('-'))
    ny, syd = ZoneInfo('America/New_York'), ZoneInfo('Australia/Sydney')
    return [dt(y, m, d, h, mi, tzinfo=ny).astimezone(syd).strftime('%H:%M') for h, mi in ((9, 35), (11, 30), (13, 0))]


def card(session=None, data=DATA):
    files = sorted((data / 'focus').glob('*.json'))
    if session:
        files = [f for f in files if f.stem == session]
    doc = json.load(open(files[-1]))
    day = datetime.strptime(doc['session'], '%Y-%m-%d').strftime('%a %-d %b')
    pre = {p['symbol']: p for p in (doc.get('premarket') or {}).get('items') or []}
    lines = [f'Focus for {day}']
    for it in sorted(doc['items'], key=lambda x: x['rank']):
        res, sup = it.get('resistance') or {}, it.get('support') or {}
        up = it.get('breakout_above') if it.get('breakout_above') is not None else res.get('price')
        dn = it.get('hold_above') if it.get('hold_above') is not None else sup.get('price')
        head = f"**#{it['rank']} {it['symbol']}** ({BIAS_WORD[it['bias']]})"
        if up is not None and dn is not None:
            head += f": ceiling {lv(up, res.get('name'))}, floor {lv(dn, sup.get('name'))}. No trade between them."
        p = pre.get(it['symbol'])
        if p and p.get('price') is not None:
            head += f" Pre-market {p['price']:.2f}: {p.get('note') or ''}".rstrip()
        if it['symbol'] in MWF_ONLY:
            same_day = datetime.strptime(doc['session'], '%Y-%m-%d').weekday() in (0, 2, 4)
            head += (' Options expire today.' if same_day else ' No options expire today: use the next day\'s expiry and size down.')
            head += ' Premiums near the money usually run over $2, so go further out or skip.'
        lines.append(head)
        for kind, lvl_, nxt in (('call', up, it.get('target_up')), ('put', dn, it.get('target_down'))):
            sp = side_plan(kind, lvl_, it.get('atr'), nxt)
            if sp:
                lines.append('- ' + side_line(kind, sp))
    first, lf, lt = sydney_times(doc['session'])
    lines.append(f'Skip a name while it chops between its ceiling and floor. No entries before {first} or between {lf} and '
                 f'{lt} Sydney. Option under $2.00, so the -25% stop loses at most $50; out after 5 minutes if it is not '
                 'working; 5 trades a day max.')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('step', choices=['pick', 'pre', 'card'])
    ap.add_argument('--session')
    ap.add_argument('--prices', help='SYM=price,... (pre step)')
    ap.add_argument('--data', default=str(DATA))
    a = ap.parse_args()
    if a.session and not re.match(r'^\d{4}-\d{2}-\d{2}$', a.session):
        sys.exit('--session must be YYYY-MM-DD')
    if a.step == 'card':
        print(card(a.session, Path(a.data)))
        return
    if a.step == 'pick':
        out, doc = pick(a.session, Path(a.data))
    else:
        prices = {k.strip().upper(): float(v) for k, v in (x.split('=') for x in (a.prices or '').split(',') if x)}
        out, doc = pre(prices, a.session, Path(a.data))
    print(f"focus {a.step}: session {doc['session']} -> {out}")
    for it in doc['items']:
        print(f"  #{it['rank']} {it['symbol']:5} {it['bias']:8} break>{it['breakout_above']} hold>{it['hold_above']} score {it['score']}")


if __name__ == '__main__':
    main()
