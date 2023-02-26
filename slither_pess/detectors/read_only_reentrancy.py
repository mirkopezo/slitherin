""""
    Re-entrancy detection
    Based on heuristics, it may lead to FP and FN
    Iterate over all the nodes of the graph until reaching a fixpoint
"""
from collections import namedtuple, defaultdict
from typing import Dict, List, Set
from slither.core.variables.variable import Variable
from slither.core.declarations import Function
from slither.core.cfg.node import NodeType, Node, Contract
from slither.detectors.abstract_detector import DetectorClassification
from .reentrancy.reentrancy import (
    Reentrancy,
    to_hashable,
    AbstractState,
    union_dict,
    _filter_if,
    is_subset,
    dict_are_equal,
)
from slither.slithir.operations import Send, Transfer, EventCall
from slither.slithir.operations import Call

FindingKey = namedtuple("FindingKey", ["function", "calls"])
FindingValue = namedtuple("FindingValue", ["variable", "written_at", "node", "nodes"])


class ReadOnlyReentrancyState(AbstractState):
    def __init__(self):
        super().__init__()
        self._reads_external: Dict[Variable, Set[Node]] = defaultdict(set)
        self._reads_external_contract_list: Dict[Variable, Set[Contract]] = defaultdict(
            set
        )
        self._written_external: Dict[Variable, Set[Node]] = defaultdict(set)
        self._written: Dict[Variable, Set[Node]] = defaultdict(set)

    @property
    def reads_external(self) -> Dict[Variable, Set[Node]]:
        return self._reads_external

    @property
    def reads_external_contract_list(self) -> Dict[Variable, Set[Contract]]:
        return self._reads_external_contract_list

    @property
    def written_external(self) -> Dict[Variable, Set[Node]]:
        return self._written_external

    @property
    def written(self) -> Dict[Variable, Set[Node]]:
        return self._written

    def add(self, fathers):
        super().add(fathers)
        self._reads_external = union_dict(self._reads_external, fathers.reads_external)
        self._reads_external_contract_list = union_dict(
            self._reads_external_contract_list, fathers.reads_external_contract_list
        )

    def does_not_bring_new_info(self, new_info):
        return (
            super().does_not_bring_new_info(new_info)
            and is_subset(new_info.reads_external, self._reads_external)
            and is_subset(
                new_info.reads_external_contract_list,
                self._reads_external_contract_list,
            )
        )

    def merge_fathers(self, node, skip_father, detector):
        for father in node.fathers:
            if detector.KEY in father.context:
                self._send_eth = union_dict(
                    self._send_eth,
                    {
                        key: values
                        for key, values in father.context[detector.KEY].send_eth.items()
                        if key != skip_father
                    },
                )
                self._calls = union_dict(
                    self._calls,
                    {
                        key: values
                        for key, values in father.context[detector.KEY].calls.items()
                        if key != skip_father
                    },
                )
                # self._reads = union_dict(
                #     self._reads, father.context[detector.KEY].reads
                # )
                # self._reads_prior_calls = union_dict(
                #     self.reads_prior_calls,
                #     father.context[detector.KEY].reads_prior_calls,
                # )
                # self._reads_external = union_dict(
                #     self._reads_external, father.context[detector.KEY].reads_external
                # )
                # self._reads_external_contract_list = union_dict(
                #     self._reads_external_contract_list,
                #     father.context[detector.KEY].reads_external_contract_list,
                # )

    def analyze_node(self, node: Node, detector):
        state_vars_read: Dict[Variable, Set[Node]] = defaultdict(
            set, {v: {node} for v in node.state_variables_read}
        )

        # All the state variables written
        state_vars_written: Dict[Variable, Set[Node]] = defaultdict(
            set, {v: {node} for v in node.state_variables_written}
        )

        external_state_vars_read: Dict[Variable, Set[Node]] = defaultdict(set)
        external_state_vars_written: Dict[Variable, Set[Node]] = defaultdict(set)
        external_state_vars_read_contract_list: Dict[
            Variable, Set[Contract]
        ] = defaultdict(set)

        slithir_operations = []
        # Add the state variables written in internal calls
        for internal_call in node.internal_calls:
            # Filter to Function, as internal_call can be a solidity call
            if isinstance(internal_call, Function):
                for internal_node in internal_call.all_nodes():
                    for read in internal_node.state_variables_read:
                        state_vars_read[read].add(internal_node)
                    for write in internal_node.state_variables_written:
                        state_vars_written[write].add(internal_node)
                slithir_operations += internal_call.all_slithir_operations()

        for contract, v in node.high_level_calls:
            if isinstance(v, Function):
                for internal_node in v.all_nodes():
                    for read in internal_node.state_variables_read:
                        external_state_vars_read[read].add(internal_node)
                        external_state_vars_read_contract_list[read].add(contract)

                    if internal_node.context.get(detector.KEY):
                        for r in internal_node.context[detector.KEY].reads_external:
                            external_state_vars_read[r].add(internal_node)
                            external_state_vars_read_contract_list[r].add(contract)
                    for write in internal_node.state_variables_written:
                        external_state_vars_written[write].add(internal_node)

        contains_call = False

        self._written = state_vars_written
        self._written_external = external_state_vars_written
        for ir in node.irs + slithir_operations:
            if detector.can_callback(ir):
                self._calls[node] |= {ir.node}
                contains_call = True

            if detector.can_send_eth(ir):
                self._send_eth[node] |= {ir.node}

            if isinstance(ir, EventCall):
                self._events[ir] |= {ir.node, node}

        self._reads = union_dict(self._reads, state_vars_read)
        self._reads_external = union_dict(
            self._reads_external, external_state_vars_read
        )
        self._reads_external_contract_list = union_dict(
            self._reads_external_contract_list, external_state_vars_read_contract_list
        )

        return contains_call


class ReadOnlyReentrancy(Reentrancy):
    ARGUMENT = "readonly-reentrancy"
    HELP = "Read-only reentrancy vulnerabilities"
    IMPACT = DetectorClassification.LOW
    CONFIDENCE = DetectorClassification.MEDIUM

    WIKI = "-"

    WIKI_TITLE = "Read-only reentrancy vulnerabilities"

    # region wiki_description
    WIKI_DESCRIPTION = """
Detection of the [reentrancy bug](https://github.com/trailofbits/not-so-smart-contracts/tree/master/reentrancy).
Only report reentrancy that acts as a double call (see `reentrancy-eth`, `reentrancy-no-eth`)."""
    # endregion wiki_description

    # region wiki_exploit_scenario
    WIKI_EXPLOIT_SCENARIO = """
```solidity
    contract MinimalReeentrant {
    uint256 private _number;

    function vulnarableGetter() public view returns (uint256) {
        return _number;
    }

    function reentrancyExploitable() public {
        msg.sender.call("");
        _number++;
    }
}

contract MinimalVictim {
    address public reentrant;

    function doSmth() public {
        MinimalReeentrant reentrant = MinimalReeentrant(reentrant);
        uint256 x = reentrant.vulnarableGetter() + 1;
    }
}
```
`_number variable is read when not finalized"""
    # endregion wiki_exploit_scenario

    WIKI_RECOMMENDATION = "Apply the [`check-effects-interactions` pattern](http://solidity.readthedocs.io/en/v0.4.21/security-considerations.html#re-entrancy)."

    STANDARD_JSON = False
    KEY = "readonly_reentrancy"

    contracts_read_variable: Dict[Variable, Set[Contract]] = defaultdict(set)
    contracts_written_variable_after_reentrancy: Dict[
        Variable, Set[Contract]
    ] = defaultdict(set)

    def _explore(self, node, visited, skip_father=None):
        if node in visited:
            return

        visited = visited + [node]

        fathers_context = ReadOnlyReentrancyState()
        fathers_context.merge_fathers(node, skip_father, self)

        # Exclude path that dont bring further information
        if node in self.visited_all_paths:
            if self.visited_all_paths[node].does_not_bring_new_info(fathers_context):
                return
        else:
            self.visited_all_paths[node] = ReadOnlyReentrancyState()

        self.visited_all_paths[node].add(fathers_context)

        node.context[self.KEY] = fathers_context

        contains_call = fathers_context.analyze_node(node, self)
        node.context[self.KEY] = fathers_context

        sons = node.sons
        if contains_call and node.type in [NodeType.IF, NodeType.IFLOOP]:
            if _filter_if(node):
                son = sons[0]
                self._explore(son, visited, node)
                sons = sons[1:]
            else:
                son = sons[1]
                self._explore(son, visited, node)
                sons = [sons[0]]

        for son in sons:
            self._explore(son, visited)

    def find_writes_after_reentrancy(self):
        written_after_reentrancy: Dict[Variable, Set[Node]] = defaultdict(set)
        written_after_reentrancy_external: Dict[Variable, Set[Node]] = defaultdict(set)
        for contract in self.contracts:
            for f in contract.functions_and_modifiers_declared:
                for node in f.nodes:
                    # dead code
                    if self.KEY not in node.context:
                        continue
                    if node.context[self.KEY].calls:
                        if not any(n != node for n in node.context[self.KEY].calls):
                            continue
                        # TODO: check if written items exist
                        for v, nodes in node.context[self.KEY].written.items():
                            written_after_reentrancy[v].add(node)
                            self.contracts_written_variable_after_reentrancy[v].add(
                                contract
                            )
                        for v, nodes in node.context[self.KEY].written_external.items():
                            written_after_reentrancy_external[v].add(node)
                            self.contracts_written_variable_after_reentrancy[v].add(
                                contract
                            )

        return written_after_reentrancy, written_after_reentrancy_external

    # IMPORTANT:
    # FOR the external reads, that var should be external written in the same contract
    def get_readonly_reentrancies(self):
        (
            written_after_reentrancy,
            written_after_reentrancy_external,
        ) = self.find_writes_after_reentrancy()
        result = defaultdict(set)
        for contract in self.contracts:
            for f in contract.functions_and_modifiers_declared:
                for node in f.nodes:

                    if self.KEY not in node.context:
                        continue
                    vulnerable_variables = set()
                    for r, nodes in node.context[self.KEY].reads.items():
                        if r.contract == f.contract and not f.view:
                            continue

                        if r in written_after_reentrancy:
                            vulnerable_variables.add(
                                FindingValue(
                                    r,
                                    tuple(
                                        sorted(
                                            list(written_after_reentrancy[r]),
                                            key=lambda x: x.node_id,
                                        )
                                    ),
                                    node,
                                    tuple(sorted(nodes, key=lambda x: x.node_id)),
                                )
                            )

                    for r, nodes in node.context[self.KEY].reads_external.items():
                        if r in written_after_reentrancy_external:
                            isVulnerable = any(
                                c in self.contracts_written_variable_after_reentrancy[r]
                                for c in node.context[
                                    self.KEY
                                ].reads_external_contract_list[r]
                            )
                            if isVulnerable:
                                vulnerable_variables.add(
                                    FindingValue(
                                        r,
                                        tuple(
                                            sorted(
                                                list(
                                                    written_after_reentrancy_external[r]
                                                ),
                                                key=lambda x: x.node_id,
                                            )
                                        ),
                                        node,
                                        tuple(sorted(nodes, key=lambda x: x.node_id)),
                                    )
                                )
                                print(
                                    f"{f.name} is vulnerable, reads {r}, which is written after reentrancy. in {node}"
                                )

                        if r in written_after_reentrancy:
                            vulnerable_variables.add(
                                FindingValue(
                                    r,
                                    tuple(
                                        sorted(
                                            list(written_after_reentrancy[r]),
                                            key=lambda x: x.node_id,
                                        )
                                    ),
                                    node,
                                    tuple(sorted(nodes, key=lambda x: x.node_id)),
                                )
                            )
                            print(
                                f"{f.name} is vulnerable, external reads {r}, which is written after reentrancy. in {node}"
                            )

                    if vulnerable_variables:
                        finding_key = FindingKey(
                            function=f, calls=to_hashable(node.context[self.KEY].calls)
                        )
                        result[finding_key] |= vulnerable_variables
        return result

    def _detect(self):  # pylint: disable=too-many-branches
        """"""
        results = []
        try:
            super()._detect()
            reentrancies = self.get_readonly_reentrancies()

            result_sorted = sorted(
                list(reentrancies.items()), key=lambda x: x[0].function.name
            )

            varsRead: List[FindingValue]
            for (func, calls), varsRead in result_sorted:

                varsRead = sorted(
                    varsRead, key=lambda x: (x.variable.name, x.node.node_id)
                )

                info = ["Readonly-Reentrancy in ", func, ":\n"]

                info += [
                    "\tState variables read that were written after the external call(s):\n"
                ]
                for finding_value in varsRead:
                    info += ["\t- ", finding_value.node, "\n"]
                    for other_node in finding_value.nodes:
                        if other_node != finding_value.node:
                            info += ["\t\t- ", other_node, "\n"]
                    info += ["\t Written after external call at:\n"]
                    for other_node in finding_value.written_at:
                        # info += ["\t- ", call_info, "\n"]
                        if other_node != finding_value.node:
                            info += ["\t\t- ", other_node, "\n"]

                # Create our JSON result
                res = self.generate_result(info)

                res.add(func)

                # Add all variables written via nodes which write them.
                for finding_value in varsRead:
                    res.add(
                        finding_value.node,
                        {
                            "underlying_type": "variables_written",
                            "variable_name": finding_value.variable.name,
                        },
                    )
                    for other_node in finding_value.nodes:
                        if other_node != finding_value.node:
                            res.add(
                                other_node,
                                {
                                    "underlying_type": "variables_written",
                                    "variable_name": finding_value.variable.name,
                                },
                            )

                # Append our result
                results.append(res)
        except Exception as e:
            info = [
                "Error during detection of readonly-reentrancy:\n",
                "Please inform this to Yhtyyar\n",
                f"error details:",
                e,
            ]
            res = self.generate_result(info)
            results.append(res)
            print(e)

        return results
