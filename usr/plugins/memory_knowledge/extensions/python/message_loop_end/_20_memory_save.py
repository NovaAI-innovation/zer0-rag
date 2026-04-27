from __future__ import annotations

from helpers.extension import Extension

from usr.plugins.memory_knowledge.helpers.extensions import extension_agent, remember_error
from usr.plugins.memory_knowledge.helpers.automation import create_episodic_memory, promote_candidate_memory, reinforce_similar_memory, upsert_subjects_from_turn
from usr.plugins.memory_knowledge.helpers.extractor import write_candidate
from usr.plugins.memory_knowledge.helpers.recorder import finish_turn, record_step
from usr.plugins.memory_knowledge.helpers.runtime import ensure_runtime


class MemorySaveExtension(Extension):
    def execute(self, **kwargs):
        agent = extension_agent(self)
        try:
            runtime = ensure_runtime(agent)
            if not runtime or not runtime.enabled:
                return
            result = finish_turn(agent, runtime, kwargs)
            candidate = write_candidate(agent, runtime, kwargs)
            episodic = create_episodic_memory(agent, runtime, kwargs)
            reinforced_candidate = reinforce_similar_memory(runtime, candidate)
            reinforced_episodic = reinforce_similar_memory(runtime, episodic)
            promoted_candidate = promote_candidate_memory(runtime, candidate, reason="semantic_candidate_confidence")
            promoted_episodic = promote_candidate_memory(runtime, episodic, reason="episodic_turn_confidence")
            subjects = upsert_subjects_from_turn(agent, runtime, kwargs)
            record_step(
                runtime,
                name="memory_lifecycle_finalize",
                step_type="memory",
                kwargs={
                    "messages_recorded": result.get("messages", 0),
                    "candidate_memory_id": candidate.get("id") if candidate else None,
                    "episodic_memory_id": episodic.get("id") if episodic else None,
                    "reinforced_candidate_memory_id": reinforced_candidate.get("id") if reinforced_candidate else None,
                    "reinforced_episodic_memory_id": reinforced_episodic.get("id") if reinforced_episodic else None,
                    "promoted_candidate_memory_id": promoted_candidate.get("id") if promoted_candidate else None,
                    "promoted_episodic_memory_id": promoted_episodic.get("id") if promoted_episodic else None,
                    "subjects_upserted": len(subjects),
                },
            )
        except Exception as exc:
            remember_error(agent, exc)
