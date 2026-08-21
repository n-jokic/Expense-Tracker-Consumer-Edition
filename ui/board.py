"""
ui/board.py — GroupedBoard v2 (Phase 2 U3).

Extracted card board. GroupedBoard does NOT write to DB — it returns a
BoardResult; the caller interprets cross-group moves (recurring → category)
or layout changes (dashboard → layout_state). Extracted from the
utils.draggable_card_board CCv2 component with BoardResult/ItemMove types
and capability flags.

Allows the orchestrator's capability matrix:
  Dashboard  → group reorder only
  Wishlist/Recurring → group + item + cross-group
  Savings/Loans/Budgets/Portfolio → per spec collapse/reorder flags
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import streamlit as st


@dataclass(frozen=True)
class ItemMove:
    id: str
    group: str
    position: int


@dataclass(frozen=True)
class BoardAction:
    id: str
    action: str
    value: Any | None = None


@dataclass
class BoardResult:
    group_order: list[str] = field(default_factory=list)
    collapsed_groups: set[str] = field(default_factory=set)
    item_order: dict[str, list[str]] = field(default_factory=dict)
    moved_items: list[ItemMove] = field(default_factory=list)
    action: BoardAction | None = None


def _validate_grouped_order(order: dict | None, expected: dict) -> dict | None:
    if not isinstance(order, dict) or set(order) != set(expected):
        return None
    wanted = [str(x) for ids in expected.values() for x in ids]
    received = [str(x) for cat in expected for x in order.get(cat, [])]
    if len(received) != len(wanted) or len(set(received)) != len(received):
        return None
    if set(received) != set(wanted):
        return None
    return {str(k): [str(x) for x in order[k]] for k in expected}


_CARD_BOARD = None


def grouped_board(
    key: str,
    groups: dict[str, list[dict]],
    *,
    allow_group_reorder: bool = True,
    allow_item_reorder: bool = True,
    allow_cross_group_move: bool = True,
    collapsible: bool = True,
) -> BoardResult:
    """Render the draggable card board (CCv2) and return a BoardResult.

    - groups: {group_id: [card{id,title,details,amount,actions:[{label,action,type,value,options}]}]}
    - Caps: when a cap is False, any move violating it is rejected (original returned).
    - Also normalizes collapsed_groups if component reports them; otherwise empty.
    """
    global _CARD_BOARD
    original: dict[str, list[str]] = {
        str(g): [str(c["id"]) for c in cards] for g, cards in groups.items()
    }
    # Lazy component load so tests that don't render UI can still import this module
    if _CARD_BOARD is None:
        _CARD_BOARD = st.components.v2.component(
            "expense_tracker_draggable_cards",
            html="<div id='board'></div>",
            css="""
                .board{display:grid;gap:1rem}
                .group{border:1px solid var(--st-border-color);border-radius:.5rem;padding:.75rem;background:var(--st-secondary-background-color)}
                .group h3{margin:0 0 .5rem;font-size:1rem}
                .drop{min-height:3rem;display:grid;gap:.5rem}
                .card{display:grid;grid-template-columns:auto 1fr auto;gap:.75rem;align-items:start;padding:.75rem;border:1px solid var(--st-border-color);border-radius:.4rem;background:var(--st-background-color);color:var(--st-text-color)}
                .card:focus{outline:2px solid var(--st-primary-color)}
                .handle{cursor:grab;border:0;background:transparent;color:var(--st-text-color);font-size:1.1rem}
                .meta{color:var(--st-secondary-text-color);font-size:.85rem}
                .amount{font-weight:600;white-space:nowrap}
                .actions{grid-column:2 / -1;display:flex;gap:.4rem;flex-wrap:wrap}
                .actions button,.actions select{font:inherit;color:inherit;background:var(--st-secondary-background-color);border:1px solid var(--st-border-color);border-radius:.25rem;padding:.25rem .5rem}
                @media(max-width:600px){.card{grid-template-columns:auto 1fr}.amount{grid-column:2}.actions{grid-column:1 / -1}}
            """,
            js="""
export default function({data,parentElement,setStateValue,setTriggerValue}) {
 const root=parentElement.querySelector('#board'); root.replaceChildren(); root.className='board';
 let drag=null;
 const emit=()=>setStateValue('order',Object.fromEntries([...root.querySelectorAll('.group')].map(g=>[g.dataset.category,[...g.querySelectorAll('.card')].map(c=>c.dataset.id)])));
 const move=(card,delta)=>{const cards=[...card.parentElement.children],i=cards.indexOf(card),to=i+delta;if(to<0||to>=cards.length)return; card.parentElement.insertBefore(card,delta<0?cards[to]:cards[to].nextSibling);emit();card.focus();};
 for(const [category,cards] of Object.entries(data.groups||{})){
  const group=document.createElement('section');group.className='group';group.dataset.category=category;const title=document.createElement('h3');title.textContent=category;const drop=document.createElement('div');drop.className='drop';group.append(title,drop);
  drop.ondragover=e=>e.preventDefault();drop.ondrop=e=>{e.preventDefault();if(drag){drop.append(drag);emit();}};
  for(const dataCard of cards){const card=document.createElement('article');card.className='card';card.dataset.id=dataCard.id;card.tabIndex=0;card.draggable=true;card.ondragstart=()=>drag=card;card.ondragend=()=>drag=null;
   card.onkeydown=e=>{if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();move(card,e.key==='ArrowUp'?-1:1);}};
   const handle=document.createElement('button');handle.className='handle';handle.type='button';handle.textContent='\u2195';handle.title='Drag, or Alt+Up / Alt+Down to move';handle.setAttribute('aria-label','Move '+dataCard.title);
   const body=document.createElement('div');const name=document.createElement('strong');name.textContent=dataCard.title;const meta=document.createElement('div');meta.className='meta';meta.textContent=dataCard.details;body.append(name,meta);
   const amount=document.createElement('div');amount.className='amount';amount.textContent=dataCard.amount;const actions=document.createElement('div');actions.className='actions';
   for(const action of dataCard.actions||[]){if(action.type==='select'){const select=document.createElement('select');select.setAttribute('aria-label',action.label);for(const value of action.options){const option=document.createElement('option');option.value=value;option.textContent=value;option.selected=value===action.value;select.append(option);}select.onchange=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:select.value});actions.append(select);}else{const button=document.createElement('button');button.type='button';button.textContent=action.label;button.onclick=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:action.value||null});actions.append(button);}}
   card.append(handle,body,amount,actions);drop.append(card);
  } root.append(group);
 } return ()=>{};
}""",
        )

    # Component invocation — data slot is groups. We pass it directly and
    # let the component emit 'order' + 'action' trigger values.
    result = _CARD_BOARD(
        data={"groups": groups},
        key=key,
        default={"order": original},
        on_order_change=lambda: None,
    )
    order_raw = getattr(result, "order", None)
    order = _validate_grouped_order(order_raw, original) or dict(original)

    # Enforce capability caps
    if not allow_item_reorder and not allow_group_reorder and not allow_cross_group_move:
        # No reordering at all — must equal original
        if order != original:
            order = dict(original)
    elif not allow_cross_group_move:
        # Cross-group move: any id appears in a different group than original
        orig_group_of = {iid: g for g, ids in original.items() for iid in ids}
        new_group_of = {iid: g for g, ids in order.items() for iid in ids}
        if any(orig_group_of.get(i) != new_group_of.get(i) for i in orig_group_of):
            order = dict(original)
        elif not allow_item_reorder:
            # Allow group reorder only — item order within each group must match
            for g in original:
                if order.get(g, []) != original[g]:
                    order = dict(original)
                    break

    # Collapsed groups — component doesn't report yet; keep empty for now.
    collapsed: set[str] = set()
    try:
        cg = getattr(result, "collapsed_groups", None)
        if isinstance(cg, (list, set, tuple)):
            collapsed = {str(x) for x in cg}
    except Exception:
        pass

    # Moved items: diff original vs returned order
    moved: list[ItemMove] = []
    if order != original:
        for g, ids in order.items():
            for pos, iid in enumerate(ids):
                orig_pos = None
                orig_g = None
                for og, oids in original.items():
                    if iid in oids:
                        orig_g = og
                        orig_pos = oids.index(iid)
                        break
                if orig_g != g or orig_pos != pos:
                    moved.append(ItemMove(id=str(iid), group=str(g), position=pos))

    # Action trigger
    action = None
    try:
        raw = getattr(result, "action", None)
        if isinstance(raw, dict) and {"id", "action"} <= set(raw):
            # Validate id belongs to known set
            known = {iid for ids in original.values() for iid in ids}
            if str(raw["id"]) in known:
                action = BoardAction(id=str(raw["id"]), action=str(raw["action"]), value=raw.get("value"))
    except Exception:
        action = None

    # Back-compat aliases
    group_order = list(order.keys())
    item_order = {k: list(v) for k, v in order.items()}

    return BoardResult(
        group_order=group_order,
        collapsed_groups=collapsed,
        item_order=item_order,
        moved_items=moved,
        action=action,
    )


# Back-compat alias for pages still importing from utils
draggable_card_board = grouped_board
