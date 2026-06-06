from pydantic import BaseModel


class BankingStatement(BaseModel):
    balance: str
    transactions: list[Transaction]
    recommendation: str


class Transaction(BaseModel):
    id: str
    to_account: str
    from_account: str
    amount: str
