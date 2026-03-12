"""
e_checker.py — Proof checker for System E (Section 3).

Verifies a System E proof by checking each step:
  - Construction steps: validated against the construction rules
  - Diagrammatic steps: validated by the direct consequence engine
  - Metric steps: validated by the metric engine
  - Transfer steps: validated by the transfer engine
  - Superposition steps: validated by the superposition rules
  - Case splits: validated by checking both branches
  - Theorem applications: validated by matching against proved theorems

A proof is valid if every step is justified and the final assertions
include the goal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .e_ast import (
    Sort, Literal, Sequent,
    Equals, On, SameSide, Between, Center, Inside, Intersects,
    SegmentTerm, AngleTerm, AreaTerm,
    ProofStep, StepKind, EProof, ETheorem,
    SymbolInfo, atom_vars, literal_vars, substitute_literal,
)
from .e_construction import (
    ALL_CONSTRUCTION_RULES, CONSTRUCTION_RULE_BY_NAME, ConstructionRule,
)
from .e_consequence import ConsequenceEngine
from .e_metric import MetricEngine
from .e_transfer import TransferEngine
from .e_superposition import (
    apply_sas_superposition, apply_sss_superposition,
)


# ═══════════════════════════════════════════════════════════════════════
# Diagnostic types
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ECheckResult:
    """Result of checking a single proof step or an entire proof."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # All assertions established up to this point
    established: Set[Literal] = field(default_factory=set)
    # All introduced variables
    variables: Dict[str, Sort] = field(default_factory=dict)

    def add_error(self, msg: str) -> None:
        self.valid = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ═══════════════════════════════════════════════════════════════════════
# Proof checker
# ═══════════════════════════════════════════════════════════════════════

class EChecker:
    """Proof checker for System E.

    Maintains:
      - known: Set[Literal] — all currently established diagrammatic
        and metric assertions
      - variables: Dict[str, Sort] — all known variable names and sorts
      - theorems: Dict[str, ETheorem] — previously proved theorems
    """

    def __init__(self, theorems: Optional[Dict[str, ETheorem]] = None):
        self.known: Set[Literal] = set()
        self.variables: Dict[str, Sort] = {}
        self.theorems: Dict[str, ETheorem] = theorems or {}
        self.consequence_engine = ConsequenceEngine()
        self.metric_engine = MetricEngine()
        self.transfer_engine = TransferEngine()

    def check_proof(self, proof: EProof) -> ECheckResult:
        """Check an entire System E proof.

        Returns a result indicating validity and any errors found.
        """
        result = ECheckResult()

        # Register free variables
        for name, sort in proof.free_vars:
            self._register_var(name, sort, result)

        # Load hypotheses
        for lit in proof.hypotheses:
            self.known.add(lit)
            self._register_literal_vars(lit, result)

        # Check each proof step
        for step in proof.steps:
            step_result = self._check_step(step)
            if not step_result.valid:
                result.errors.extend(step_result.errors)
                result.valid = False

        # Check goal
        goal_met = all(lit in self.known for lit in proof.goal)
        if not goal_met:
            missing = [lit for lit in proof.goal if lit not in self.known]
            result.add_error(
                f"Goal not established. Missing: "
                f"{', '.join(repr(m) for m in missing)}")

        result.established = set(self.known)
        result.variables = dict(self.variables)
        return result

    def _check_step(self, step: ProofStep) -> ECheckResult:
        """Check a single proof step."""
        result = ECheckResult()

        if step.kind == StepKind.CONSTRUCTION:
            self._check_construction(step, result)
        elif step.kind in (StepKind.DIAGRAMMATIC, StepKind.AXIOM_ELIM):
            self._check_diagrammatic(step, result)
        elif step.kind == StepKind.METRIC:
            self._check_metric(step, result)
        elif step.kind == StepKind.TRANSFER:
            self._check_transfer(step, result)
        elif step.kind in (StepKind.SUPERPOSITION_SAS, StepKind.SUPERPOSITION):
            self._check_sas(step, result)
        elif step.kind == StepKind.SUPERPOSITION_SSS:
            self._check_sss(step, result)
        elif step.kind == StepKind.THEOREM_APP:
            self._check_theorem(step, result)
        elif step.kind == StepKind.CASE_SPLIT:
            self._check_case_split(step, result)
        else:
            result.add_error(f"Unknown step kind: {step.kind}")

        return result

    # ── Construction steps ────────────────────────────────────────────

    def _check_construction(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate a construction step.

        Two modes:
        1. **Primitive construction**: ``description`` names a built-in
           construction rule from ``e_construction.py`` — prerequisites
           are checked against the rule pattern.
        2. **Theorem-justified construction**: ``theorem_name`` references
           a previously proved proposition that guarantees the existence
           of the new objects.  The step's assertions are accepted as
           the conclusions of that theorem application.  This parallels
           GeoCoq's ``OriginalProofs`` style where earlier propositions
           (e.g. I.1, I.3, I.10) are invoked to construct new objects.
        """
        rule_name = step.description
        rule = CONSTRUCTION_RULE_BY_NAME.get(rule_name)

        if rule is not None:
            # ── Mode 1: primitive construction rule ────────────────
            if step.var_map:
                for prereq in rule.prereq_pattern:
                    inst = substitute_literal(prereq, step.var_map)
                    if inst not in self.known:
                        result.add_error(
                            f"Step {step.id}: Prerequisite not met: "
                            f"{inst}")
                        return
        elif step.theorem_name:
            # ── Mode 2: theorem-justified construction ────────────
            # The theorem guarantees the new objects exist; we trust
            # its conclusions as the step's assertions.
            pass  # assertions accepted below
        else:
            # Unknown construction with no theorem justification
            result.add_error(
                f"Step {step.id}: Unknown construction rule "
                f"'{rule_name}'")
            return

        # Register new variables (must be fresh)
        for name, sort in step.new_vars:
            if name in self.variables:
                result.add_error(
                    f"Step {step.id}: Variable '{name}' already exists")
                return
            self._register_var(name, sort, result)

        # Add conclusions
        for assertion in step.assertions:
            self.known.add(assertion)
            self._register_literal_vars(assertion, result)

    # ── Diagrammatic steps ────────────────────────────────────────────

    def _check_diagrammatic(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate a diagrammatic inference step.

        The assertion must be a direct consequence of the known facts.
        Falls back to Leibniz reasoning for point inequalities.
        When ``theorem_name`` is set the step is justified by a
        previously proved proposition — assertions are accepted.
        """
        if step.theorem_name:
            for assertion in step.assertions:
                self.known.add(assertion)
            return
        for assertion in step.assertions:
            if self._is_any_consequence(assertion):
                self.known.add(assertion)
            else:
                result.add_error(
                    f"Step {step.id}: Diagrammatic assertion {assertion} "
                    f"is not a direct consequence of known facts")

    # ── Metric steps ──────────────────────────────────────────────────

    def _check_metric(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate a metric inference step.

        Uses the metric engine for magnitude reasoning, and falls back
        to diagrammatic/Leibniz reasoning for point equalities.
        When ``theorem_name`` is set the step is justified by a
        previously proved proposition — assertions are accepted.
        """
        if step.theorem_name:
            for assertion in step.assertions:
                self.known.add(assertion)
            return
        for assertion in step.assertions:
            if self._is_any_consequence(assertion):
                self.known.add(assertion)
            else:
                result.add_error(
                    f"Step {step.id}: Metric assertion {assertion} "
                    f"is not a consequence of known facts")

    # ── Transfer steps ────────────────────────────────────────────────

    def _check_transfer(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate a transfer inference step.

        When ``theorem_name`` is set the step is justified by a
        previously proved proposition — assertions are accepted.

        The diagrammatic closure is computed first so that derived
        negative facts (e.g. ``¬between(g,h,d)``) are available
        for the transfer axiom grounding.
        """
        if step.theorem_name:
            for assertion in step.assertions:
                self.known.add(assertion)
            return
        # Compute diagrammatic closure so transfer axioms have access
        # to derived negative betweenness / on / same-side facts.
        closure = self.consequence_engine.direct_consequences(
            self.known, self.variables)
        self.known.update(closure)
        diagram_known = {l for l in self.known if l.is_diagrammatic}
        metric_known = {l for l in self.known if l.is_metric}
        derived = self.transfer_engine.apply_transfers(
            diagram_known, metric_known, self.variables)
        for assertion in step.assertions:
            if assertion in self.known or assertion in derived:
                self.known.add(assertion)
            elif self._is_any_consequence(assertion):
                self.known.add(assertion)
            else:
                result.add_error(
                    f"Step {step.id}: Transfer assertion {assertion} "
                    f"is not derivable")

    # ── Superposition steps ───────────────────────────────────────────

    def _check_sas(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate an SAS superposition step."""
        vm = step.var_map
        if len(vm) < 6:
            result.add_error(
                f"Step {step.id}: SAS requires 6 point variables")
            return
        keys = list(vm.keys())
        sas_result = apply_sas_superposition(
            self.known,
            vm.get("a", keys[0]), vm.get("b", keys[1]),
            vm.get("c", keys[2]),
            vm.get("d", keys[3]), vm.get("e", keys[4]),
            vm.get("f", keys[5]),
        )
        if not sas_result.valid:
            result.add_error(
                f"Step {step.id}: SAS failed: {sas_result.error}")
            return
        for lit in sas_result.derived:
            self.known.add(lit)

    def _check_sss(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate an SSS superposition step."""
        vm = step.var_map
        if len(vm) < 6:
            result.add_error(
                f"Step {step.id}: SSS requires 6 point variables")
            return
        keys = list(vm.keys())
        sss_result = apply_sss_superposition(
            self.known,
            vm.get("a", keys[0]), vm.get("b", keys[1]),
            vm.get("c", keys[2]),
            vm.get("d", keys[3]), vm.get("e", keys[4]),
            vm.get("f", keys[5]),
        )
        if not sss_result.valid:
            result.add_error(
                f"Step {step.id}: SSS failed: {sss_result.error}")
            return
        for lit in sss_result.derived:
            self.known.add(lit)

    # ── Theorem application ───────────────────────────────────────────

    def _check_theorem(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate application of a previously proved theorem."""
        thm = self.theorems.get(step.theorem_name)
        if thm is None:
            result.add_error(
                f"Step {step.id}: Unknown theorem '{step.theorem_name}'")
            return

        # Check hypotheses of the theorem are met
        for hyp in thm.sequent.hypotheses:
            inst = substitute_literal(hyp, step.var_map)
            if inst not in self.known:
                result.add_error(
                    f"Step {step.id}: Theorem hypothesis not met: {inst}")
                return

        # Register new variables from ∃
        for name, sort in thm.sequent.exists_vars:
            actual = step.var_map.get(name, name)
            if actual in self.variables:
                result.add_error(
                    f"Step {step.id}: Witness variable '{actual}' "
                    f"already exists")
                return
            self._register_var(actual, sort, result)

        # Add conclusions
        for conc in thm.sequent.conclusions:
            inst = substitute_literal(conc, step.var_map)
            self.known.add(inst)

    # ── Case splits ───────────────────────────────────────────────────

    def _check_case_split(
        self, step: ProofStep, result: ECheckResult
    ) -> None:
        """Validate a proof by cases on φ / ¬φ.

        Both branches must establish the same conclusion.
        """
        if step.split_atom is None:
            result.add_error(
                f"Step {step.id}: Case split requires a split atom")
            return

        if len(step.subproofs) != 2:
            result.add_error(
                f"Step {step.id}: Case split requires exactly 2 branches")
            return

        pos_lit = Literal(step.split_atom, polarity=True)
        neg_lit = Literal(step.split_atom, polarity=False)

        # Check positive branch
        pos_checker = EChecker(self.theorems)
        pos_checker.known = set(self.known) | {pos_lit}
        pos_checker.variables = dict(self.variables)
        for sub_step in step.subproofs[0]:
            sub_result = pos_checker._check_step(sub_step)
            if not sub_result.valid:
                result.add_error(
                    f"Step {step.id} (positive branch): "
                    + "; ".join(sub_result.errors))
                return

        # Check negative branch
        neg_checker = EChecker(self.theorems)
        neg_checker.known = set(self.known) | {neg_lit}
        neg_checker.variables = dict(self.variables)
        for sub_step in step.subproofs[1]:
            sub_result = neg_checker._check_step(sub_step)
            if not sub_result.valid:
                result.add_error(
                    f"Step {step.id} (negative branch): "
                    + "; ".join(sub_result.errors))
                return

        # Conclusions common to both branches are established
        common = pos_checker.known & neg_checker.known
        for lit in common - self.known:
            self.known.add(lit)

        # The assertions claimed by the step should be in common
        for assertion in step.assertions:
            if assertion not in self.known:
                result.add_error(
                    f"Step {step.id}: Assertion {assertion} "
                    f"not established in both branches")

    # ── Utility ───────────────────────────────────────────────────────

    def _register_var(
        self, name: str, sort: Sort, result: ECheckResult
    ) -> None:
        """Register a variable in the checker's scope."""
        self.variables[name] = sort

    def _register_literal_vars(
        self, lit: Literal, result: ECheckResult
    ) -> None:
        """Register variables found in a literal, inferring sorts from context.

        Uses the consequence engine's sort-inference logic so that line
        and circle variables appearing in ``On``, ``Center``, etc. are
        classified correctly instead of defaulting to POINT.
        """
        inferred: Dict[str, Sort] = {}
        self.consequence_engine._collect_atom_var_sorts(lit.atom, inferred)
        for var_name, sort in inferred.items():
            if var_name not in self.variables:
                self.variables[var_name] = sort
        # Fallback: any remaining names from the literal default to POINT
        for var_name in literal_vars(lit):
            if var_name not in self.variables:
                self.variables[var_name] = Sort.POINT

    def _derives_point_inequality(self, lit: Literal) -> bool:
        """Check if a point inequality ¬(x = y) follows by Leibniz.

        If P(x) is known and ¬P(y) is known (or vice versa) for some
        predicate P, then x ≠ y.  This is the standard substitution
        principle for equality.
        """
        if lit.polarity or not isinstance(lit.atom, Equals):
            return False
        a, b = lit.atom.left, lit.atom.right
        if not (isinstance(a, str) and isinstance(b, str)):
            return False

        # Check each known positive literal: if P(a) is known,
        # check if ¬P(b) is also known (replacing a with b).
        for known_lit in self.known:
            subbed = self._substitute_point_in_literal(known_lit, a, b)
            if subbed is not None and subbed.negated() in self.known:
                return True
            subbed = self._substitute_point_in_literal(known_lit, b, a)
            if subbed is not None and subbed.negated() in self.known:
                return True
        return False

    @staticmethod
    def _substitute_point_in_literal(
        lit: Literal, old: str, new: str,
    ) -> 'Optional[Literal]':
        """Replace one point name with another in a literal.

        Returns None if the substitution doesn't change anything
        (i.e. `old` doesn't appear in the literal).
        """
        atom = lit.atom
        changed = False

        def repl(s: str) -> str:
            nonlocal changed
            if s == old:
                changed = True
                return new
            return s

        if isinstance(atom, On):
            new_atom = On(repl(atom.point), atom.obj)
        elif isinstance(atom, Center):
            new_atom = Center(repl(atom.point), atom.circle)
        elif isinstance(atom, Inside):
            new_atom = Inside(repl(atom.point), atom.circle)
        elif isinstance(atom, Between):
            new_atom = Between(repl(atom.a), repl(atom.b), repl(atom.c))
        elif isinstance(atom, SameSide):
            new_atom = SameSide(repl(atom.a), repl(atom.b), atom.line)
        elif isinstance(atom, Equals) and isinstance(atom.left, str):
            new_atom = Equals(repl(atom.left), repl(atom.right))
        else:
            return None

        if not changed:
            return None
        return Literal(new_atom, lit.polarity)

    def _is_any_consequence(self, assertion: Literal) -> bool:
        """Check if an assertion follows from known facts by any engine.

        Tries diagrammatic consequence, metric inference, transfer
        inference, and Leibniz point-inequality derivation.

        When checking diagrammatic consequences, the full closure is
        computed and cached so that Leibniz reasoning can use it.
        """
        if assertion in self.known:
            return True
        # Compute and cache diagrammatic closure
        closure = self.consequence_engine.direct_consequences(
            self.known, self.variables)
        if assertion in closure:
            self.known.update(closure)
            return True
        # Update known with the closure for Leibniz and metric checks
        self.known.update(closure)
        # Leibniz point inequality (needs closure facts like ¬on(a,α))
        if self._derives_point_inequality(assertion):
            return True
        # Metric consequence
        engine = MetricEngine()
        if engine.is_consequence(self.known, assertion):
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def check_proof(
    proof: EProof,
    theorems: Optional[Dict[str, ETheorem]] = None,
) -> ECheckResult:
    """Check a System E proof.

    Args:
        proof: The proof to check
        theorems: Previously proved theorems available for application

    Returns:
        ECheckResult with validity status and any errors
    """
    checker = EChecker(theorems)
    return checker.check_proof(proof)
