"""
ui/board.py — GroupedBoard v2 (Phase 2 U3 / FIN-03).

Extracted card board. GroupedBoard does NOT write to DB — it returns a
BoardResult; the caller interprets cross-group moves (recurring → category),
layout changes (recurring group order/collapse → layout_state), or
(dashboard → layout_state). Extracted from the utils.draggable_card_board
CCv2 component with BoardResult/ItemMove types and capability flags.

Allows the orchestrator's capability matrix:
  Dashboard  → group reorder only
  Wishlist/Recurring → group + item + cross-group
  Savings/Loans/Budgets/Portfolio → per spec collapse/reorder flags

FIN-03: the component now also reports GROUP order and collapsed groups,
with keyboard-operable native buttons and aria-expanded state on every
group control. All values returned by the component are validated here
before the caller persists them.
"""

from __future__ import annotations

import inspect
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


def _validate_group_order(seq: Any, known: set[str]) -> list[str] | None:
    """A valid group order is a permutation of the known group ids."""
    if not isinstance(seq, (list, tuple)):
        return None
    vals = [str(x) for x in seq]
    if len(vals) != len(set(vals)):
        return None
    if set(vals) != set(str(k) for k in known):
        return None
    return vals


def _validate_collapsed(seq: Any, known: set[str]) -> set[str]:
    """Collapsed groups must be a subset of the known group ids."""
    if not isinstance(seq, (list, tuple, set)):
        return set()
    return {str(x) for x in seq if str(x) in known}


def apply_persisted_group_order(category_order: list[str],
                                persisted_order: list[str] | None) -> list[str]:
    """Merge a persisted group order into the current category list.

    Persisted ids that still exist come first (in their stored order);
    new/unknown categories keep their natural order after them. Duplicates
    in the persisted value are ignored. Pure helper — used by pages before
    rendering so the board opens in the saved arrangement.
    """
    known = set(category_order)
    seen: set[str] = set()
    head: list[str] = []
    for g in persisted_order or []:
        g = str(g)
        if g in known and g not in seen:
            head.append(g)
            seen.add(g)
    return head + [g for g in category_order if g not in seen]


def grouped_board(
    key: str,
    groups: dict[str, list[dict]],
    *,
    allow_group_reorder: bool = True,
    allow_item_reorder: bool = True,
    allow_cross_group_move: bool = True,
    collapsible: bool = True,
    initial_collapsed: list[str] | None = None,
    initial_group_order: list[str] | None = None,
) -> BoardResult:
    """Render the draggable card board (CCv2) and return a BoardResult.

    - groups: {group_id: [card{id,title,details,amount,actions:[{label,action,type,value,options}]}]}
    - Caps: when a cap is False, any move violating it is rejected (original returned).
    - initial_collapsed / initial_group_order seed the component with persisted
      state (FIN-03); the component emits `order`, `group_order`, and
      `collapsed_groups`, all re-validated here before they reach the caller.
    """
    original: dict[str, list[str]] = {
        str(g): [str(c["id"]) for c in cards] for g, cards in groups.items()
    }
    known_groups = set(original)
    seed_order = apply_persisted_group_order(list(original), initial_group_order)
    seed_collapsed = _validate_collapsed(initial_collapsed, known_groups)
    # A3 fix: register on EVERY render. The bidi component registry lives on
    # the ACTIVE Runtime instance, so a module-level cache of the mounted
    # callable goes stale across AppTest runs / runtime restarts and mounts
    # fail with "Component 'expense_tracker_draggable_cards' is not registered".
    _card_board = st.components.v2.component(
            "expense_tracker_draggable_cards",
            html="<div id='board'></div>",
            css="""
                .board{display:grid;gap:1rem}
                .group{border:1px solid var(--st-border-color);border-radius:.5rem;padding:.75rem;background:var(--st-secondary-background-color)}
                .ghead{display:flex;align-items:center;gap:.4rem;margin:0 0 .5rem}
                .group h3{margin:0;font-size:1rem;flex:1}
                .drop{min-height:3rem;display:grid;gap:.5rem}
                .card{display:grid;grid-template-columns:auto 1fr auto;gap:.75rem;align-items:start;padding:.75rem;border:1px solid var(--st-border-color);border-radius:.4rem;background:var(--st-background-color);color:var(--st-text-color)}
                .card:focus{outline:2px solid var(--st-primary-color)}
                .handle{cursor:grab;border:0;background:transparent;color:var(--st-text-color);font-size:1.1rem}
                .gtoggle{border:0;background:transparent;color:var(--st-text-color);font-size:1rem;cursor:pointer;padding:0 .2rem}
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
 const collapsed=new Set(data.collapsed_groups||[]);
 const emit=()=>{
  setStateValue('order',Object.fromEntries([...root.querySelectorAll('.group')].map(g=>[g.dataset.category,[...g.querySelectorAll('.card')].map(c=>c.dataset.id)])));
  setStateValue('group_order',[...root.querySelectorAll('.group')].map(g=>g.dataset.category));
  setStateValue('collapsed_groups',[...collapsed]);
 };
 const moveCard=(card,delta)=>{const cards=[...card.parentElement.children],i=cards.indexOf(card),to=i+delta;if(to<0||to>=cards.length)return;card.parentElement.insertBefore(card,delta<0?cards[to]:cards[to].nextSibling);emit();card.focus();};
 const moveGroup=(group,delta)=>{const gs=[...root.children],i=gs.indexOf(group),to=i+delta;if(to<0||to>=gs.length)return;root.insertBefore(group,delta<0?gs[to]:gs[to].nextSibling);emit();};
 const entries=Object.entries(data.groups||{});
 const savedOrder=Array.isArray(data.group_order)?data.group_order.filter(c=>Object.prototype.hasOwnProperty.call(data.groups||{},c)):[];
 const seen=new Set(savedOrder);
 const seq=[...savedOrder,...entries.map(([c])=>c).filter(c=>!seen.has(c))];
 for(const category of seq){
  const cards=data.groups[category]||[];
  const group=document.createElement('section');group.className='group';group.dataset.category=category;
  const drop=document.createElement('div');drop.className='drop';drop.style.display=collapsed.has(category)?'none':'';
  const head=document.createElement('div');head.className='ghead';
  const toggle=document.createElement('button');toggle.type='button';toggle.className='gtoggle';
  const setToggleState=()=>{toggle.textContent=collapsed.has(category)?'\\u25B6':'\\u25BC';toggle.setAttribute('aria-expanded',collapsed.has(category)?'false':'true');};
  toggle.setAttribute('aria-label',(collapsed.has(category)?'Expand':'Collapse')+' group '+category);
  setToggleState();
  toggle.onclick=()=>{if(collapsed.has(category)){collapsed.delete(category);}else{collapsed.add(category);}setToggleState();drop.style.display=collapsed.has(category)?'none':'';emit();};
  const title=document.createElement('h3');title.textContent=category;
  const up=document.createElement('button');up.type='button';up.className='handle';up.textContent='\\u2191';
  up.title='Move group '+category+' up';up.setAttribute('aria-label','Move group '+category+' up');up.onclick=()=>moveGroup(group,-1);
  const down=document.createElement('button');down.type='button';down.className='handle';down.textContent='\\u2193';
  down.title='Move group '+category+' down';down.setAttribute('aria-label','Move group '+category+' down');down.onclick=()=>moveGroup(group,1);
  head.append(toggle,title,up,down);
  drop.ondragover=e=>e.preventDefault();drop.ondrop=e=>{e.preventDefault();if(drag){drop.append(drag);emit();}};
  for(const dataCard of cards){const card=document.createElement('article');card.className='card';card.dataset.id=dataCard.id;card.tabIndex=0;card.draggable=true;card.ondragstart=()=>drag=card;card.ondragend=()=>drag=null;
   card.onkeydown=e=>{if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();moveCard(card,e.key==='ArrowUp'?-1:1);}};
   const handle=document.createElement('button');handle.className='handle';handle.type='button';handle.textContent='\\u2195';handle.title='Drag, or Alt+Up / Alt+Down to move';handle.setAttribute('aria-label','Move '+dataCard.title);
   const body=document.createElement('div');const name=document.createElement('strong');name.textContent=dataCard.title;const meta=document.createElement('div');meta.className='meta';meta.textContent=dataCard.details;body.append(name,meta);
   const amount=document.createElement('div');amount.className='amount';amount.textContent=dataCard.amount;const actions=document.createElement('div');actions.className='actions';
   for(const action of dataCard.actions||[]){if(action.type==='select'){const select=document.createElement('select');select.setAttribute('aria-label',action.label);for(const value of action.options){const option=document.createElement('option');option.value=value;option.textContent=value;option.selected=value===action.value;select.append(option);}select.onchange=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:select.value});actions.append(select);}else{const button=document.createElement('button');button.type='button';button.textContent=action.label;button.onclick=()=>setTriggerValue('action',{id:dataCard.id,action:action.action,value:action.value||null});actions.append(button);}}
   card.append(handle,body,amount,actions);drop.append(card);
  }
  group.append(head,drop);
  root.append(group);
 } return ()=>{};
}""",
        )

    # Component invocation — data slot carries groups plus the persisted
    # group state so the board opens exactly as saved.
    result = _card_board(
        data={"groups": groups,
              "collapsed_groups": sorted(seed_collapsed),
              "group_order": seed_order},
        key=key,
        default={"order": original,
                 "collapsed_groups": sorted(seed_collapsed),
                 "group_order": seed_order},
        # A3 fix: every state key listed in `default` (and emitted via
        # setStateValue by the component JS) must have a matching
        # on_<state>_change callback, or Streamlit rejects the invocation
        # with "Key '<name>' in `default` is not a valid state name" —
        # which made the canonical board crash on EVERY run and silently
        # fall back to a non-draggable list.
        on_order_change=lambda: None,
        on_group_order_change=lambda: None,
        on_collapsed_groups_change=lambda: None,
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

    # Group order (FIN-03): must be a permutation of the known groups,
    # otherwise fall back to the seeded/original arrangement.
    group_order = _validate_group_order(getattr(result, "group_order", None),
                                        known_groups)
    if group_order is None:
        group_order = seed_order if allow_group_reorder else list(original)

    # Collapsed groups (FIN-03): subset of known groups; respect the
    # collapsible cap by reporting the seeded state when collapse is off.
    collapsed = _validate_collapsed(getattr(result, "collapsed_groups", None),
                                    known_groups)
    if not collapsible:
        collapsed = set(seed_collapsed)

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

    # Back-compat alias: item_order keyed in group_order sequence
    item_order = {k: list(order[k]) for k in group_order if k in order}

    return BoardResult(
        group_order=group_order,
        collapsed_groups=collapsed,
        item_order=item_order,
        moved_items=moved,
        action=action,
    )


def component_source_contract() -> str:
    """Source of the registered component JS (test contract for a11y)."""
    return inspect.getsource(grouped_board)


# Back-compat alias for pages still importing from utils
draggable_card_board = grouped_board
