"""Scaffold a working stateful bookstore demo without any AI or remote calls."""

import sqlite3
from contextlib import closing
from pathlib import Path
from reqlica.projects.store import create_project, Manifest, Endpoint
from reqlica.projects.versions import code_directory, atomic_write_json

SOURCE = '''from fastapi import HTTPException
from pydantic import BaseModel, Field
from reqlica.runtime.sdk import connection, transaction

class Book(BaseModel):
    id: int
    title: str
    stock: int

class OrderInput(BaseModel):
    book_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=100)

class Order(BaseModel):
    id: int
    book_id: int
    quantity: int

def books() -> list[Book]:
    with connection() as db:
        return [Book(**dict(row)) for row in db.execute("SELECT * FROM books ORDER BY id")]

def order(body: OrderInput) -> Order:
    with transaction() as db:
        if db.execute("SELECT id FROM books WHERE id=?", (body.book_id,)).fetchone() is None:
            raise HTTPException(404, "Book not found")
        changed = db.execute("UPDATE books SET stock=stock-? WHERE id=? AND stock>=?", (body.quantity, body.book_id, body.quantity)).rowcount
        if not changed:
            raise HTTPException(409, "Insufficient stock")
        cursor = db.execute("INSERT INTO orders(book_id,quantity) VALUES(?,?)", (body.book_id,body.quantity))
        return Order(id=cursor.lastrowid, book_id=body.book_id, quantity=body.quantity)
'''
TESTS = '''import unittest
from fastapi import HTTPException
from endpoints.books import books, order, OrderInput
from reqlica.runtime.sdk import transaction, connection

class BookstoreTests(unittest.TestCase):
    def test_order_decrements_stock_and_rejects_overdraw(self):
        with transaction() as db:
            book_id = db.execute("INSERT INTO books(title,stock) VALUES('Test fixture',2)").lastrowid
        result = order(OrderInput(book_id=book_id, quantity=1))
        self.assertEqual(result.quantity, 1)
        with self.assertRaises(HTTPException) as failure:
            order(OrderInput(book_id=book_id, quantity=2))
        self.assertEqual(failure.exception.status_code,409)
        with connection() as db:
            self.assertEqual(db.execute("SELECT stock FROM books WHERE id=?", (book_id,)).fetchone()[0],1)
        self.assertTrue(any(book.id == book_id for book in books()))
'''


def create_demo(directory):
    project = create_project(Path(directory), "Bookstore demo")
    code = code_directory(project)
    (code / "endpoints/books.py").write_text(SOURCE)
    (code / "tests/test_bookstore.py").write_text(TESTS)
    manifest = Manifest.model_validate_json((code / "manifest.json").read_bytes())
    manifest.endpoints.extend([Endpoint(method="GET", path="/books", handler="endpoints/books.py:books"),
                               Endpoint(method="POST", path="/orders", handler="endpoints/books.py:order")])
    atomic_write_json(code / "manifest.json", manifest.model_dump())
    with closing(sqlite3.connect(project.directory / "data.sqlite")) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT NOT NULL, stock INTEGER NOT NULL CHECK(stock>=0))")
        db.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id), quantity INTEGER NOT NULL CHECK(quantity>0))")
        db.executemany("INSERT INTO books(title,stock) VALUES(?,?)", [("A Study in Scarlet",5),("Pride and Prejudice",3)])
    return project
