"""The books: balanced entries, keyed postings, reversals, and the arithmetic of money."""

import random
import uuid
from datetime import datetime

import pytest

from app.core import ledger as books
from app.core.ledger import (
    CASH,
    EXPENSE_HOSTING,
    GST,
    INCOME_MEMBERSHIP,
    INCOME_OTHER,
    OWED,
    KeyReused,
    Line,
    Unbalanced,
    credit,
    debit,
)
from app.core.payments.money import proportional, split_gst, tip_split
from app.database import SessionLocal
from app.models.ledger import LedgerEntry, LedgerTransaction
from app.models.user import User

def window(year, month):
    """[start, end) of a calendar month. Years before 2024 belong to no real month, so a test that gives
    itself its own year never sees (or disturbs) the entries other tests and modules have made."""
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return datetime(year, month, 1), end


def key() -> str:
    return f"test:{uuid.uuid4().hex}"


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def receive(db, amount, account=INCOME_OTHER, when=datetime(2001, 1, 15), k=None):
    txn = books.post(db, k or key(), "manual", [debit(CASH, amount), credit(account, amount)], posted_at=when)
    db.commit()
    return txn


# --------------------------------------------------------------------------- a transaction must balance

def test_entries_that_do_not_add_up_are_refused_and_nothing_is_stored(db):
    k = key()
    with pytest.raises(Unbalanced):
        books.post(db, k, "manual", [debit(CASH, 100), credit(INCOME_OTHER, 90)])
    assert db.query(LedgerTransaction).filter(LedgerTransaction.key == k).first() is None


def test_zero_lines_are_dropped_and_an_all_zero_transaction_is_refused(db):
    txn = books.post(db, key(), "manual", [debit(CASH, 500), credit(INCOME_OTHER, 500), credit(GST, 0)], posted_at=datetime(2001, 1, 2))
    assert [e.account for e in txn.entries] == [CASH, INCOME_OTHER]
    with pytest.raises(Unbalanced):
        books.post(db, key(), "manual", [debit(CASH, 0), credit(INCOME_OTHER, 0)])
    db.rollback()


def test_unknown_accounts_and_a_writer_balance_without_a_writer_are_refused(db):
    with pytest.raises(Unbalanced):
        books.post(db, key(), "manual", [debit(CASH, 5), credit("made_up", 5)])
    with pytest.raises(Unbalanced):
        books.post(db, key(), "manual", [debit(CASH, 5), credit(OWED, 5)])  # owed to whom?


def test_the_helpers_sign_the_sides_whatever_the_caller_passes(db):
    assert debit(CASH, -7) == Line(CASH, 7) and credit(CASH, -7) == Line(CASH, -7) and credit(CASH, 7).amount == -7


# --------------------------------------------------------------------------- one key, one transaction

def test_posting_the_same_event_twice_is_one_transaction(db):
    k = key()
    first = receive(db, 250, k=k)
    second = books.post(db, k, "manual", [debit(CASH, 250), credit(INCOME_OTHER, 250)])
    db.commit()
    assert second.id == first.id
    assert db.query(LedgerEntry).filter(LedgerEntry.txn_id == first.id).count() == 2


def test_a_key_reused_with_other_amounts_stops_instead_of_dropping_money(db):
    k = key()
    receive(db, 250, k=k)
    with pytest.raises(KeyReused):
        books.post(db, k, "manual", [debit(CASH, 999), credit(INCOME_OTHER, 999)])
    db.rollback()


# --------------------------------------------------------------------------- undoing

def test_a_reversal_mirrors_the_original_in_the_same_month_and_both_are_kept(db):
    span = window(2002, 1)
    original = receive(db, 4000, when=datetime(2002, 1, 15))
    assert books.balance(db, INCOME_OTHER, start=span[0], end=span[1]) == 4000

    mirror = books.reverse(db, original, admin_id=None)
    db.commit()
    assert mirror.reverses_id == original.id and mirror.id != original.id and mirror.posted_at == original.posted_at
    assert books.balance(db, INCOME_OTHER, start=span[0], end=span[1]) == 0
    assert db.query(LedgerTransaction).filter(LedgerTransaction.id == original.id).first() is not None
    # Reversing again hands back the first reversal; it never double-undoes.
    assert books.reverse(db, original, admin_id=None).id == mirror.id


# --------------------------------------------------------------------------- reading the books

def test_balances_read_the_way_people_read_them_and_by_period(db):
    jan, feb = window(2003, 1), window(2003, 2)
    receive(db, 1000, INCOME_MEMBERSHIP, datetime(2003, 1, 10))
    receive(db, 300, INCOME_MEMBERSHIP, datetime(2003, 2, 10))
    books.post(db, key(), "manual", [debit(EXPENSE_HOSTING, 120), credit(CASH, 120)], posted_at=datetime(2003, 1, 20))
    db.commit()

    january = books.totals_by_account(db, *jan)
    assert january[INCOME_MEMBERSHIP] == 1000 and january[EXPENSE_HOSTING] == 120  # income and costs both read as positive
    assert january[CASH] == 1000 - 120
    assert books.totals_by_account(db, *feb)[INCOME_MEMBERSHIP] == 300
    assert books.split_by_kind(db, *jan) == (1000, 120)


def two_writers(db):
    """Two real accounts (the books refer to people by id, and a database that checks foreign keys insists they exist)."""
    tag = uuid.uuid4().hex[:8]
    people = [User(email=f"owed{n}_{tag}@example.com", username=f"owed{n}_{tag}", hashed_password="x") for n in (1, 2)]
    db.add_all(people)
    db.commit()
    return people[0].id, people[1].id


def test_what_each_writer_is_owed_is_kept_per_writer(db):
    a, b = two_writers(db)
    books.post(db, key(), "tip", [debit(CASH, 900), credit(OWED, 600, a), credit(OWED, 300, b)])
    books.post(db, key(), "payout", [debit(OWED, 250, a), credit(CASH, 250)])
    db.commit()
    assert books.balance(db, OWED, user_id=a) == 350 and books.balance(db, OWED, user_id=b) == 300
    assert books.owed_to_each(db)[a] == 350


def test_the_whole_ledger_always_sums_to_zero(db):
    receive(db, 77)
    assert books.is_balanced(db)


# --------------------------------------------------------------------------- the arithmetic

@pytest.mark.parametrize("bps", [0, 250, 1000, 1234, 10000])
def test_a_tip_always_splits_into_exactly_the_tip(bps):
    for gross in list(range(1, 400)) + [random.randint(1, 10**7) for _ in range(300)]:
        writer, site = tip_split(gross, bps)
        assert writer + site == gross and writer >= 0 and site >= 0


def test_the_site_share_rounds_half_up():
    assert tip_split(1000, 1000) == (900, 100)
    assert tip_split(15, 1000) == (13, 2)   # 1.5 paise -> 2
    assert tip_split(14, 1000) == (13, 1)   # 1.4 paise -> 1


def test_gst_inside_a_price():
    assert split_gst(4900, 0) == (4900, 0)
    net, tax = split_gst(4900, 18)
    assert (net, tax) == (4153, 747) and net + tax == 4900
    for price in range(1, 3000):
        net, tax = split_gst(price, 18)
        assert net + tax == price and 0 <= tax <= price


def test_a_part_of_a_whole_rounds_half_up_and_a_full_part_is_exact():
    assert proportional(2000, 5000, 4500) == 1800
    assert proportional(5000, 5000, 4500) == 4500
    assert proportional(1, 0, 100) == 0
