"""The seven graph nodes.

Four of them form the loop (plan, execute, observe, reflect). Router
runs before it, synthesize and groundedness after it.

Each node has a contract, stated in its own module docstring:
  reads   — which state fields it consumes
  returns — which state fields it writes
  model   — which tier, or none
  never   — what it must not do
"""

from ops_copilot.agent.nodes.router import router_node
from ops_copilot.agent.nodes.plan import plan_node
from ops_copilot.agent.nodes.execute import execute_node
from ops_copilot.agent.nodes.observe import observe_node
from ops_copilot.agent.nodes.reflect import reflect_node
from ops_copilot.agent.nodes.synthesize import synthesize_node
from ops_copilot.agent.nodes.groundedness import groundedness_node

__all__ = [
    "router_node", "plan_node", "execute_node", "observe_node",
    "reflect_node", "synthesize_node", "groundedness_node",
]
