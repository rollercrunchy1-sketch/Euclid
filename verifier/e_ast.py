"""
e_ast.py — AST for the formal system E (Avigad, Dean, Mumma 2009).

System E is six-sorted:
  - Points (a, b, c, ...): geometric points
  - Lines  (L, M, N, ...): straight lines
  - Circles (α, β, γ, ...): circles
  - Segments: magnitudes representing segment lengths (ab)
  - Angles:   magnitudes representing angle measures (∠abc)
  - Areas:    magnitudes representing triangle areas  (△abc)

The first three sorts have variables; the last three (magnitude sorts)
are constructed from point terms and support +, <, =, and a constant
``right-angle``.

Formulas in E are conjunctions of *literals* (atomic or negated atomic).
Theorems are sequents:  Γ ⇒ ∃x̄. Δ
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union


# ═══════════════════════════════════════════════════════════════════════
# Sorts
# ═══════════════════════════════════════════════════════════════════════

class Sort(Enum):
    """The six sorts of System E."""
    POINT = auto()
    LINE = auto()
    CIRCLE = auto()
    SEGMENT = auto()   # magnitude sort
    ANGLE = auto()      # magnitude sort
    AREA = auto()       # magnitude sort


DIAGRAM_SORTS = frozenset({Sort.POINT, Sort.LINE, Sort.CIRCLE})
MAGNITUDE_SORTS = frozenset({Sort.SEGMENT, Sort.ANGLE, Sort.AREA})


# ═══════════════════════════════════════════════════════════════════════
# Terms  (variables and magnitude expressions)
# ═══════════════════════════════════════════════════════════════════════

class Term:
    """Base class for all terms."""
    pass


@dataclass(frozen=True)
class Var(Term):
    """A variable of one of the three diagram sorts."""
    name: str
    sort: Sort

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class SegmentTerm(Term):
    """Magnitude: length of segment from point a to point b.  Written ab."""
    p1: str  # point variable name
    p2: str  # point variable name

    def __repr__(self) -> str:
        return f"{self.p1}{self.p2}"


@dataclass(frozen=True)
class AngleTerm(Term):
    """Magnitude: measure of angle ∠abc.  Written ∠abc."""
    p1: str  # point variable name
    p2: str  # vertex
    p3: str  # point variable name

    def __repr__(self) -> str:
        return f"\u2220{self.p1}{self.p2}{self.p3}"


@dataclass(frozen=True)
class AreaTerm(Term):
    """Magnitude: area of triangle △abc.  Written △abc."""
    p1: str
    p2: str
    p3: str

    def __repr__(self) -> str:
        return f"\u25b3{self.p1}{self.p2}{self.p3}"


@dataclass(frozen=True)
class MagAdd(Term):
    """Sum of two magnitude terms (same magnitude sort). a + b."""
    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"({self.left} + {self.right})"


@dataclass(frozen=True)
class RightAngle(Term):
    """The constant ``right-angle`` of the angle magnitude sort."""

    def __repr__(self) -> str:
        return "right-angle"


@dataclass(frozen=True)
class ZeroMag(Term):
    """The constant 0 for a magnitude sort."""
    sort: Sort  # which magnitude sort

    def __repr__(self) -> str:
        return "0"


# ═══════════════════════════════════════════════════════════════════════
# Atomic formulas  (the building blocks of literals)
# ═══════════════════════════════════════════════════════════════════════

class Atom:
    """Base class for atomic formulas."""
    pass


# ── Diagram relations ─────────────────────────────────────────────────

@dataclass(frozen=True)
class On(Atom):
    """on(a, L) — point a is on line L.
       on(a, α) — point a is on circle α."""
    point: str
    obj: str  # line or circle variable name

    def __repr__(self) -> str:
        return f"on({self.point}, {self.obj})"


@dataclass(frozen=True, eq=False)
class SameSide(Atom):
    """same-side(a, b, L) — points a, b on same side of line L.

    Same-side is symmetric in its point arguments:
    SameSide(a, b, L) == SameSide(b, a, L).
    """
    a: str
    b: str
    line: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SameSide):
            return NotImplemented
        return (self.line == other.line and
                {self.a, self.b} == {other.a, other.b})

    def __hash__(self) -> int:
        return hash((frozenset((self.a, self.b)), self.line))

    def __repr__(self) -> str:
        return f"same-side({self.a}, {self.b}, {self.line})"


@dataclass(frozen=True)
class Between(Atom):
    """between(a, b, c) — b is strictly between a and c (distinct, collinear)."""
    a: str
    b: str
    c: str

    def __repr__(self) -> str:
        return f"between({self.a}, {self.b}, {self.c})"


@dataclass(frozen=True)
class Center(Atom):
    """center(a, α) — a is the center of circle α."""
    point: str
    circle: str

    def __repr__(self) -> str:
        return f"center({self.point}, {self.circle})"


@dataclass(frozen=True)
class Inside(Atom):
    """inside(a, α) — point a is inside circle α."""
    point: str
    circle: str

    def __repr__(self) -> str:
        return f"inside({self.point}, {self.circle})"


@dataclass(frozen=True, eq=False)
class Intersects(Atom):
    """intersects(X, Y) — transversal intersection of two objects.

    X, Y can be (line, line), (line, circle), or (circle, circle).
    Intersection is symmetric: Intersects(X, Y) == Intersects(Y, X).
    """
    obj1: str
    obj2: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Intersects):
            return NotImplemented
        return ({self.obj1, self.obj2} == {other.obj1, other.obj2})

    def __hash__(self) -> int:
        return hash(frozenset((self.obj1, self.obj2)))

    def __repr__(self) -> str:
        return f"intersects({self.obj1}, {self.obj2})"


# ── Equality (on any sort) ───────────────────────────────────────────

@dataclass(frozen=True, eq=False)
class Equals(Atom):
    """x = y  (same sort).  For diagram sorts this is object identity;
    for magnitude sorts this is congruence/equality of measure.

    Equality is symmetric: Equals(x, y) == Equals(y, x)."""
    left: Union[str, Term]
    right: Union[str, Term]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Equals):
            return NotImplemented
        return ({self.left, self.right} == {other.left, other.right})

    def __hash__(self) -> int:
        return hash(frozenset((self.left, self.right)))

    def __repr__(self) -> str:
        return f"{self.left} = {self.right}"


# ── Magnitude ordering ────────────────────────────────────────────────

@dataclass(frozen=True)
class LessThan(Atom):
    """x < y  for magnitude terms (segments, angles, or areas)."""
    left: Term
    right: Term

    def __repr__(self) -> str:
        return f"{self.left} < {self.right}"


# ═══════════════════════════════════════════════════════════════════════
# Literals  (atomic or negated atomic — the assertion language of E)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Literal:
    """A literal is an atomic formula or its negation.

    ``polarity=True`` means the atom is asserted;
    ``polarity=False`` means it is negated.
    """
    atom: Atom
    polarity: bool = True

    def negated(self) -> Literal:
        """Return the opposite-polarity literal."""
        return Literal(self.atom, not self.polarity)

    @property
    def is_positive(self) -> bool:
        return self.polarity

    @property
    def is_negative(self) -> bool:
        return not self.polarity

    @property
    def is_diagrammatic(self) -> bool:
        """True if the atom is a diagrammatic relation (not metric)."""
        return isinstance(self.atom, (On, SameSide, Between, Center,
                                       Inside, Intersects)) or (
            isinstance(self.atom, Equals) and
            isinstance(self.atom.left, str) and
            isinstance(self.atom.right, str))

    @property
    def is_metric(self) -> bool:
        """True if the atom involves magnitude terms."""
        return isinstance(self.atom, LessThan) or (
            isinstance(self.atom, Equals) and
            not (isinstance(self.atom.left, str) and
                 isinstance(self.atom.right, str)))

    def __repr__(self) -> str:
        if self.polarity:
            return repr(self.atom)
        return f"\u00ac({self.atom})"


# Convenience: ⊥ (contradiction / falsity)
BOTTOM = Literal(Equals("_bot", "_bot"), False)  # sentinel


@dataclass(frozen=True)
class Bottom:
    """Explicit ⊥ symbol (contradiction)."""

    def __repr__(self) -> str:
        return "\u22a5"


# ═══════════════════════════════════════════════════════════════════════
# Clauses  (used in the direct-consequence engine, Section 3.8)
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Clause:
    """A clause is a finite set of literals, read disjunctively.

    A clause {φ₁, …, φₙ} represents:  φ₁ ∨ … ∨ φₙ.
    The 'contrapositive' usage: if ¬φ₁, …, ¬φₙ₋₁ are known,
    then φₙ can be inferred.
    """
    literals: FrozenSet[Literal]

    def __repr__(self) -> str:
        return " \u2228 ".join(repr(l) for l in self.literals)


# ═══════════════════════════════════════════════════════════════════════
# Sequents  (the theorem form of System E)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Sequent:
    """Γ ⇒ ∃x̄. Δ

    - hypotheses: Γ — set of literals (the assumptions / premises)
    - exists_vars: x̄ — list of (name, sort) for existentially quantified
      constructed objects
    - conclusions: Δ — set of literals (the derived assertions)
    """
    hypotheses: List[Literal] = field(default_factory=list)
    exists_vars: List[Tuple[str, Sort]] = field(default_factory=list)
    conclusions: List[Literal] = field(default_factory=list)

    def __repr__(self) -> str:
        hyp = ", ".join(repr(l) for l in self.hypotheses)
        conc = ", ".join(repr(l) for l in self.conclusions)
        if self.exists_vars:
            evars = ", ".join(f"{n}:{s.name}" for n, s in self.exists_vars)
            return f"{hyp} \u21d2 \u2203{evars}. {conc}"
        return f"{hyp} \u21d2 {conc}"


# ═══════════════════════════════════════════════════════════════════════
# Construction Rules  (built-in sequent-form theorems)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ConstructionRule:
    """A construction rule introduces new objects.

    Formally: Γ_prereqs ⇒ ∃x̄. Δ_conclusions

    - name: human-readable description (e.g. "let a be a point on L")
    - category: "point", "line_circle", or "intersection"
    - prereq_pattern: list of literal patterns (prerequisites Γ)
    - new_vars: list of (name, sort) variables introduced
    - conclusion_pattern: list of literal patterns (conclusions Δ)
    """
    name: str
    category: str
    prereq_pattern: List[Literal]
    new_vars: List[Tuple[str, Sort]]
    conclusion_pattern: List[Literal]


# ═══════════════════════════════════════════════════════════════════════
# Proof structure
# ═══════════════════════════════════════════════════════════════════════

class StepKind(Enum):
    """The type of a proof step in System E.

    Naming follows an intro/elim convention where possible:
    - CONSTRUCTION     = object introduction (let-line, let-circle, …)
    - AXIOM_ELIM       = generic axiom elimination (legacy catch-all)
    - DIAGRAMMATIC     = derive from diagrammatic axiom schemas (§3.4)
    - METRIC           = derive from metric axiom schemas (§3.5)
    - TRANSFER         = derive from transfer axiom schemas (§3.6)
    - SUPERPOSITION_SAS= SAS superposition elimination (§3.7)
    - SUPERPOSITION_SSS= SSS superposition elimination (§3.7)
    - THEOREM_APP      = apply a previously proved proposition
    - INDIRECT         = reductio citing earlier props (legacy wrapper)
    - BOT_INTRO        = ⊥-intro: derive ⊥ from ψ and ¬ψ
    - BOT_ELIM         = ⊥-elim: discharge Assume subproof via ⊥
    """
    CONSTRUCTION = auto()      # introduces new objects
    AXIOM_ELIM = auto()        # generic axiom elimination (legacy)
    DIAGRAMMATIC = auto()      # diagrammatic axiom schemas (§3.4)
    METRIC = auto()            # metric axiom schemas (§3.5)
    TRANSFER = auto()          # transfer axiom schemas (§3.6)
    SUPERPOSITION = auto()     # SAS or SSS superposition (generic)
    SUPERPOSITION_SAS = auto() # SAS superposition (§3.7)
    SUPERPOSITION_SSS = auto() # SSS superposition (§3.7)
    THEOREM_APP = auto()       # applies a previously proved theorem
    INDIRECT = auto()          # reductio ad absurdum citing earlier props
    BOT_INTRO = auto()         # ⊥-intro: derive ⊥ from ψ and ¬ψ
    BOT_ELIM = auto()          # ⊥-elim: discharge Assume via ⊥
    CASE_SPLIT = auto()        # proof by cases

    # ── Backward-compatibility aliases ──────────────────────────────
    REDUCTIO = BOT_ELIM
    CONTRADICTION = BOT_INTRO


@dataclass
class ProofStep:
    """A single step in a System E proof.

    Each step transforms the current sequent (Γ ⇒ ∃x̄. Δ) into a new
    sequent, possibly introducing new objects or adding new assertions.
    """
    id: int
    kind: StepKind
    description: str = ""
    # The assertion(s) added by this step
    assertions: List[Literal] = field(default_factory=list)
    # New variables introduced (for construction steps)
    new_vars: List[Tuple[str, Sort]] = field(default_factory=list)
    # References to prior steps or theorems
    refs: List[int] = field(default_factory=list)
    # For case splits: the atom being split on
    split_atom: Optional[Atom] = None
    # For theorem application: the theorem name and variable renaming
    theorem_name: str = ""
    var_map: Dict[str, str] = field(default_factory=dict)
    # For superposition: the hypotheses set
    superposition_hyps: List[Literal] = field(default_factory=list)
    # Subproofs (for case splits: two branches)
    subproofs: List[List[ProofStep]] = field(default_factory=list)


@dataclass
class EProofLine:
    """A proof line in the current hybrid format (bridges old UI to new verifier).

    This allows the proof panel to continue using its existing line-based
    format while we build the System E checker underneath.
    """
    id: int
    depth: int
    statement: str  # raw text of the assertion(s)
    justification: str  # rule/step name
    refs: List[int] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    # Parsed form
    literals: List[Literal] = field(default_factory=list)


@dataclass
class SymbolInfo:
    """Information about a declared or introduced symbol."""
    name: str
    sort: Sort
    origin: str = ""  # "declaration", "construction", "witness", etc.
    introduced_at: int = 0  # proof step id


@dataclass
class ETheorem:
    """A proved theorem in System E, available for application in later proofs.

    Form: Γ ⇒ ∃x̄. Δ
    """
    name: str
    statement: str  # human-readable
    sequent: Sequent


@dataclass
class EProof:
    """A proof in System E.

    A proof establishes a sequent Γ ⇒ ∃x̄. Δ by a sequence of
    construction and demonstration steps.
    """
    name: str
    # The theorem being proved
    hypotheses: List[Literal] = field(default_factory=list)
    exists_vars: List[Tuple[str, Sort]] = field(default_factory=list)
    goal: List[Literal] = field(default_factory=list)
    # Declared free variables (universally quantified)
    free_vars: List[Tuple[str, Sort]] = field(default_factory=list)
    # The proof steps
    steps: List[ProofStep] = field(default_factory=list)
    # Bridge format lines (for compatibility with the old UI)
    lines: List[EProofLine] = field(default_factory=list)
    # Symbol table
    symbols: Dict[str, SymbolInfo] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════════

def literal_eq(a: Literal, b: Literal) -> bool:
    """Structural equality of two literals."""
    return a == b


def atom_vars(atom: Atom) -> Set[str]:
    """Return the set of variable names occurring in an atom."""
    if isinstance(atom, On):
        return {atom.point, atom.obj}
    if isinstance(atom, SameSide):
        return {atom.a, atom.b, atom.line}
    if isinstance(atom, Between):
        return {atom.a, atom.b, atom.c}
    if isinstance(atom, Center):
        return {atom.point, atom.circle}
    if isinstance(atom, Inside):
        return {atom.point, atom.circle}
    if isinstance(atom, Intersects):
        return {atom.obj1, atom.obj2}
    if isinstance(atom, Equals):
        return _term_vars(atom.left) | _term_vars(atom.right)
    if isinstance(atom, LessThan):
        return _term_vars(atom.left) | _term_vars(atom.right)
    return set()


def _term_vars(t: Union[str, Term]) -> Set[str]:
    """Variable names in a term."""
    if isinstance(t, str):
        return {t}
    if isinstance(t, Var):
        return {t.name}
    if isinstance(t, SegmentTerm):
        return {t.p1, t.p2}
    if isinstance(t, AngleTerm):
        return {t.p1, t.p2, t.p3}
    if isinstance(t, AreaTerm):
        return {t.p1, t.p2, t.p3}
    if isinstance(t, MagAdd):
        return _term_vars(t.left) | _term_vars(t.right)
    if isinstance(t, (RightAngle, ZeroMag)):
        return set()
    return set()


def literal_vars(lit: Literal) -> Set[str]:
    """All variable names in a literal."""
    return atom_vars(lit.atom)


def substitute_literal(lit: Literal, mapping: Dict[str, str]) -> Literal:
    """Apply a variable renaming to a literal."""
    return Literal(substitute_atom(lit.atom, mapping), lit.polarity)


def substitute_atom(atom: Atom, m: Dict[str, str]) -> Atom:
    """Apply a variable renaming to an atom."""
    def s(v: str) -> str:
        return m.get(v, v)

    if isinstance(atom, On):
        return On(s(atom.point), s(atom.obj))
    if isinstance(atom, SameSide):
        return SameSide(s(atom.a), s(atom.b), s(atom.line))
    if isinstance(atom, Between):
        return Between(s(atom.a), s(atom.b), s(atom.c))
    if isinstance(atom, Center):
        return Center(s(atom.point), s(atom.circle))
    if isinstance(atom, Inside):
        return Inside(s(atom.point), s(atom.circle))
    if isinstance(atom, Intersects):
        return Intersects(s(atom.obj1), s(atom.obj2))
    if isinstance(atom, Equals):
        return Equals(_sub_term(atom.left, m), _sub_term(atom.right, m))
    if isinstance(atom, LessThan):
        return LessThan(_sub_term(atom.left, m), _sub_term(atom.right, m))
    return atom


def _sub_term(t: Union[str, Term], m: Dict[str, str]) -> Union[str, Term]:
    """Apply a variable renaming to a term."""
    if isinstance(t, str):
        return m.get(t, t)
    if isinstance(t, Var):
        return Var(m.get(t.name, t.name), t.sort)
    if isinstance(t, SegmentTerm):
        return SegmentTerm(m.get(t.p1, t.p1), m.get(t.p2, t.p2))
    if isinstance(t, AngleTerm):
        return AngleTerm(m.get(t.p1, t.p1), m.get(t.p2, t.p2),
                         m.get(t.p3, t.p3))
    if isinstance(t, AreaTerm):
        return AreaTerm(m.get(t.p1, t.p1), m.get(t.p2, t.p2),
                        m.get(t.p3, t.p3))
    if isinstance(t, MagAdd):
        return MagAdd(_sub_term(t.left, m), _sub_term(t.right, m))
    return t  # RightAngle, ZeroMag — no variables


def mag_sort(t: Term) -> Optional[Sort]:
    """Return the magnitude sort of a term, or None."""
    if isinstance(t, SegmentTerm):
        return Sort.SEGMENT
    if isinstance(t, (AngleTerm, RightAngle)):
        return Sort.ANGLE
    if isinstance(t, AreaTerm):
        return Sort.AREA
    if isinstance(t, MagAdd):
        return mag_sort(t.left)
    if isinstance(t, ZeroMag):
        return t.sort
    return None
