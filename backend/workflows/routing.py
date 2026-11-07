"""Deterministic decisions. Model output cannot authorize progression."""
def after_review(state):
    review = state['review']
    if review['critical_safety']['status'] != 'PASS':
        return 'blocked'
    from backend.contracts import ReviewReport, parse_payload
    verdict = parse_payload(ReviewReport, review).overall_result
    if verdict == 'PASS':
        return 'assets'
    if verdict == 'FAIL' and state['correction_count'] < 2:
        return 'correct'
    return 'blocked'
