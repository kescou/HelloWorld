"""Kategorien (Haushaltsbuch). OWNER: Backend-Core."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.core import Category, Transaction
from app.schemas.core import CategoryCreate, CategoryOut, CategoryUpdate
from app.services import ledger

router = APIRouter()


def _get(db: Session, category_id: int) -> Category:
    cat = db.get(Category, category_id)
    if cat is None:
        raise HTTPException(404, "Kategorie nicht gefunden")
    return cat


def _clean_rules(rules: list[str] | None) -> list[str] | None:
    if rules is None:
        return None
    seen: list[str] = []
    for r in rules:
        r = str(r).strip()
        if r and r.lower() not in (s.lower() for s in seen):
            seen.append(r)
    return seen


def _name_taken(db: Session, name: str, parent_id: int | None, exclude_id: int | None = None) -> bool:
    stmt = select(Category.id).where(func.lower(Category.name) == name.lower())
    stmt = stmt.where(Category.parent_id.is_(None) if parent_id is None else Category.parent_id == parent_id)
    if exclude_id is not None:
        stmt = stmt.where(Category.id != exclude_id)
    return db.scalar(stmt) is not None


@router.get("", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(Transaction.category_id, func.count()).where(Transaction.category_id.is_not(None))
        .group_by(Transaction.category_id)
    ).all())
    out = []
    for cat in db.scalars(select(Category).order_by(Category.kind, Category.parent_id.nulls_first(), Category.name)):
        item = CategoryOut.model_validate(cat)
        item.transaction_count = counts.get(cat.id, 0)
        out.append(item)
    return out


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryCreate, db: Session = Depends(get_db)):
    if data.parent_id is not None:
        _get(db, data.parent_id)
    if _name_taken(db, data.name, data.parent_id):
        raise HTTPException(409, "Kategorie existiert bereits")
    cat = Category(name=data.name, kind=data.kind, parent_id=data.parent_id, color=data.color,
                   match_rules=_clean_rules(data.match_rules))
    db.add(cat)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Kategorie existiert bereits") from exc
    return CategoryOut.model_validate(cat)


@router.post("/auto-assign")
def auto_assign(db: Session = Depends(get_db)):
    """Apply match_rules to all uncategorised transactions."""
    updated = ledger.auto_assign_categories(db)
    db.commit()
    remaining = db.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.category_id.is_(None), Transaction.type.in_(ledger.CATEGORIZABLE_TYPES)
        )
    )
    return {"updated": updated, "uncategorized": remaining or 0}


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, data: CategoryUpdate, db: Session = Depends(get_db)):
    cat = _get(db, category_id)
    fields = data.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"] is not None:
        if fields["parent_id"] == category_id:
            raise HTTPException(422, "Kategorie kann nicht ihre eigene Oberkategorie sein")
        _get(db, fields["parent_id"])
    new_name = fields.get("name") or cat.name
    new_parent = fields["parent_id"] if "parent_id" in fields else cat.parent_id
    if (new_name != cat.name or new_parent != cat.parent_id) and _name_taken(db, new_name, new_parent, cat.id):
        raise HTTPException(409, "Kategorie existiert bereits")
    for key, value in fields.items():
        if key in ("name", "kind") and value is None:
            continue
        setattr(cat, key, _clean_rules(value) if key == "match_rules" else value)
    db.commit()
    return CategoryOut.model_validate(cat)


@router.delete("/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    """Deletes the category; its transactions become uncategorised, children move one level up."""
    cat = _get(db, category_id)
    affected = db.execute(
        Transaction.__table__.update().where(Transaction.category_id == category_id).values(category_id=None)
    ).rowcount
    for child in db.scalars(select(Category).where(Category.parent_id == category_id)):
        if _name_taken(db, child.name, cat.parent_id, child.id):
            child.name = f"{child.name} ({cat.name})"[:80]
        child.parent_id = cat.parent_id
    db.flush()
    db.delete(cat)
    db.commit()
    return {"deleted": True, "uncategorized_transactions": affected}
