"""Sub-agent definitions (YAML, organised as domain bundles).

Layout
------
This directory is the canonical single source of truth for sub-agent prompts,
tools, skills, and metadata. Agents are grouped into **bundles** — one
sub-directory per domain — so related agents and their dedicated skills live
together:

.. code-block:: text

    subagents/
    ├── core/                    # Generic research workflow bundle
    │   ├── bundle.yaml          # Bundle manifest (name, version, agents, skills)
    │   ├── planner.yaml
    │   ├── data_analysis.yaml
    │   ├── research.yaml
    │   ├── code.yaml
    │   ├── debug.yaml
    │   ├── writing.yaml
    │   ├── scheduler.yaml
    │   └── skills/              # Skills owned by this bundle
    │       ├── find-skills/
    │       └── skill-creator/
    └── solar/                   # Solar-Cycle Co-Scientist domain bundle
        ├── bundle.yaml
        ├── solar_planner.yaml
        ├── solar_data.yaml
        ├── solar_hypothesis.yaml
        ├── solar_experiment.yaml
        ├── solar_evidence.yaml
        ├── solar_knowledge.yaml
        └── skills/
            ├── knowledge-base-agent/
            └── solar-cycle/

Each ``<name>.yaml`` describes one sub-agent in the form expected by
``EvoScientist.utils.load_subagents``. The loader scans this directory
**recursively**, so adding a new bundle is just ``mkdir <bundle>/`` plus
dropping yaml files in — no code change required.

Files or directories whose name starts with ``_`` or ``.`` are ignored, which
lets a bundle ship an ``_archive/`` folder for disabled agents.

``bundle.yaml`` is bundle metadata (name, version, description, agents,
skills, depends_on) and is **not** loaded as a sub-agent definition. The
``EvoScientist.subagents._registry`` module discovers bundles and resolves
load order via topological sort of ``depends_on``.

Optional ``async: true`` on a sub-agent's yaml routes it through
``langgraph dev`` as an AsyncSubAgent when ``config.enable_async_subagents``
is set; the matching deployment binding lives in
``EvoScientist/langgraph_dev/graphs.py``, built by
``EvoScientist.subagents._factory.build_async_subagent_graph``.
"""
