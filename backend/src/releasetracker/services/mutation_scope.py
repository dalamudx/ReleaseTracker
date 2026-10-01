"""Mutation domains must use proven runtime identities, never connection aliases."""


async def mutation_resource_key(storage, scheduler, executor):
    from .deployment_readiness import resource_scope

    scope = await resource_scope(storage, executor, scheduler)
    return "deployment-mutations" if scope == "*" else f"deployment-mutations:{scope}"
