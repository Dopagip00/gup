"""Daily shadow-selection lifecycle. No trading eligibility or order execution."""
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
import re

PROTOCOL = 'intraday-selection-v1'
TZ = timezone(timedelta(hours=8))
CHECKS = ('minute_semantics', 'complete_bars', 'frozen_reference', 'pullback',
          'two_rising_closes', 'no_support_break', 'price_cap', 'volume',
          'strong_market', 'anti_chase', 'current_risks', 'raw_price_consistency')


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(TZ)


def validate(state, day, now):
    if (not isinstance(state, dict) or state.get('protocol') != PROTOCOL
            or state.get('date') != day or state.get('eligible') is not False
            or state.get('actionable') is not False):
        raise ValueError('Invalid selection state')
    stamp = instant(state['checked_at'])
    if stamp.date().isoformat() != day or stamp > now:
        raise ValueError('Invalid selection time')
    selected = state.get('selected')
    if selected is not None:
        if (not isinstance(selected, dict) or not re.fullmatch(r'sh\d{6}', selected.get('symbol', ''))
                or not isinstance(selected.get('rank'), int) or not 1 <= selected['rank'] <= 5
                or not isinstance(state.get('cohort_id'), str) or not state['cohort_id']):
            raise ValueError('Invalid locked selection')
        chosen = instant(selected['selected_at'])
        if chosen.date().isoformat() != day or not time(9,45) <= chosen.time() <= time(10) or chosen > stamp:
            raise ValueError('Invalid selection window')
    revoked = state.get('revocation')
    if revoked is not None:
        if selected is None or not isinstance(revoked, dict) or not revoked.get('reason'):
            raise ValueError('Revocation without selection or reason')
        if not instant(selected['selected_at']) <= instant(revoked['at']) <= stamp:
            raise ValueError('Invalid revocation time')
    return state


def restore(states, day, now):
    """Reject a journal that changes its locked choice or erases a revocation."""
    previous = None
    for state in sorted(states, key=lambda s: instant(s['checked_at'])):
        validate(state, day, now)
        if previous:
            if previous['selected'] is not None and (
                    state['selected'] != previous['selected'] or state['cohort_id'] != previous['cohort_id']):
                raise ValueError('Locked selection changed in journal')
            if previous['revocation'] is not None and state['revocation'] != previous['revocation']:
                raise ValueError('Revocation erased in journal')
        previous = state
    return deepcopy(previous)


def qualifying(evidence, symbol, cohort_id, now):
    """Only normalized verifier output can enter; raw source flags are not input."""
    try:
        if (not isinstance(evidence, dict) or evidence.get('symbol') != symbol
                or evidence.get('cohort_id') != cohort_id
                or evidence.get('protocol') != 'intraday-confirmation-evidence-v1'
                or any(evidence.get('checks', {}).get(key) is not True for key in CHECKS)):
            return False
        confirmed = instant(evidence['confirmed_at'])
        observed = instant(evidence['observed_at'])
        return (confirmed.date() == now.date() and time(9,45) <= confirmed.time() <= time(10)
                and confirmed <= observed <= now and now - confirmed <= timedelta(seconds=90))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def advance(previous, *, now, cohort_id, symbols, assessments, vetoes, ready, reason=None):
    """Pick first currently qualified in original rank; lock, then only revoke.

    selected_at is a research decision time, NOT proof of public visibility.
    All records remain non-actionable, including fully matching shadow signals.
    """
    if now.tzinfo is None:
        raise ValueError('Timezone required')
    now = now.astimezone(TZ)
    day = now.date().isoformat()
    if (not isinstance(symbols, list) or len(symbols) > 5 or len(set(symbols)) != len(symbols)
            or any(not isinstance(s, str) or not re.fullmatch(r'sh\d{6}', s) for s in symbols)):
        raise ValueError('Invalid original cohort')
    state = deepcopy(validate(previous, day, now)) if previous is not None else {
        'protocol': PROTOCOL, 'date': day, 'cohort_id': None, 'selected': None,
        'revocation': None, 'eligible': False, 'actionable': False,
        'published_at': None, 'valid_evaluation_samples': 0}
    state.update(checked_at=now.isoformat(), reason=reason)
    selected = state['selected']
    if state['revocation'] is not None:
        state['status'] = 'revoked'
        return state
    if selected is not None:
        risk = vetoes.get(selected['symbol'])
        if risk:
            state.update(status='revoked', revocation={'at': now.isoformat(), 'reason': risk})
        elif ready is not True or cohort_id != state['cohort_id']:
            state.update(status='suspended', reason=reason or 'cohort_or_evidence_unavailable')
        else:
            state['status'] = 'locked'
        return state
    if ready is not True or not cohort_id:
        state.update(status='blocked', reason=reason or 'evidence_unavailable')
    elif now.time() < time(9,45):
        state['status'] = 'waiting'
    elif now.time() > time(10):
        state['status'] = 'expired'
    else:
        state['status'] = 'waiting'
        for rank, symbol in enumerate(symbols, 1):
            evidence = assessments.get(symbol)
            if not vetoes.get(symbol) and qualifying(evidence, symbol, cohort_id, now):
                state.update(status='locked', cohort_id=cohort_id, reason=None,
                             selected={'symbol': symbol, 'rank': rank,
                                       'selected_at': now.isoformat(), 'evidence': deepcopy(evidence)})
                break
    return state
