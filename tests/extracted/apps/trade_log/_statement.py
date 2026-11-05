"""A literal Schwab Account Statement, small enough to read.

Every parser test loads this rather than the account, so the expected values are the
FILE's own numbers -- which is what makes "did the parse change?" answerable without a
broker. The rows are verbatim from 2026-10-06, including the two shapes that caused
real bugs: a spread whose second leg has a BLANK exec time, and a price written as
`.27` with no leading zero.

Column layouts differ per section and the leading blank column is easy to miss:
  Trade History : ,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
  Order History : Notes,,Time Placed,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,PRICE,,TIF,Status
"""
import csv
import io

STATEMENT = '''Account Statement for 40342049SCHW (Individual) since 10/6/26 through 10/6/26

Cash Balance
DATE,TIME,TYPE,REF #,DESCRIPTION,Misc Fees,Commissions & Fees,AMOUNT,BALANCE
10/6/26,13:00:00,BAL,,Cash balance at the start of business day 06.10 CST,,,,"72,355.25"

Account Order History
Notes,,Time Placed,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,PRICE,,TIF,Status
,,10/6/26 21:51:53,DIAGONAL,SELL,-6,TO OPEN,CRWD,18 DEC 26,250,PUT,10.63,LMT,DAY,FILLED
,,,RE #1008184241389,BUY,+6,TO CLOSE,CRWD,20 NOV 26,220,PUT,CREDIT,,,
,,10/6/26 21:50:02,DIAGONAL,SELL,-6,TO OPEN,CRWD,18 DEC 26,230,PUT,5.60,LMT,DAY,REJECTED: You do not have enough available cash/buying power for this order.

Account Trade History
,Exec Time,Spread,Side,Qty,Pos Effect,Symbol,Exp,Strike,Type,Price,Net Price,Order Type
,10/6/26 21:50:47,STRANGLE,BUY,+2,TO CLOSE,TLT,20 NOV 26,82,CALL,.27,.85,LMT
,,,BUY,+2,TO CLOSE,TLT,20 NOV 26,74,PUT,.58,DEBIT,
,10/6/26 21:39:20,STRANGLE,BUY,+1,TO CLOSE,META,20 NOV 26,850,CALL,12.51,18.58,LMT
,,,BUY,+1,TO CLOSE,META,20 NOV 26,630,PUT,6.07,DEBIT,

Options
Symbol,Option Code,Exp,Strike,Type,Qty,Trade Price,Mark,Mark Value
TLT,TLT261120P74,20 NOV 26,74,PUT,-7,.52,.60,($420.00)
CRWD,CRWD261218P240,18 DEC 26,240,PUT,-6,9.82,9.90,"($5,940.00)"
,OVERALL TOTALS,,,,,,,"($6,360.00)"

Account Summary
Net Liquidating Value,"$65,406.20"
Option Buying Power,($233.33)
'''


def sections():
    """The statement, split the way the loader splits it."""
    from data_providers.jobs.trades import schwab_statement as S
    return S.sections(list(csv.reader(io.StringIO(STATEMENT), strict=False)))
