# Agent Safety: Fork-Local Development

This fork is the isolated development environment for agentic work.

## Default rule

ALL DEVELOPMENT IS FORK-LOCAL.

`DT-UCPH/cuc` is read-only unless the human user explicitly authorizes a final upstream action.

Agents must never create upstream pull requests, issues, comments, review requests, mentions, branches, workflow runs, releases, or repository-setting changes as part of an autonomous development loop.

## Allowed without additional approval

- read from `DT-UCPH/cuc` for research, comparison, synchronization, and review;
- create branches, commits, and internal pull requests in `alexsosn/cuc`;
- run fork-local CI when useful;
- perform research -> plan -> TDD -> implementation -> test -> independent adversarial review loops entirely in the fork.

## Requires explicit human authorization

Any write whose destination is `DT-UCPH/cuc`, including:

- opening or updating an upstream pull request;
- creating or modifying upstream issues;
- posting comments or review comments;
- requesting reviews or mentioning collaborators;
- pushing branches or commits;
- dispatching or modifying upstream workflows;
- changing releases, settings, rulesets, or metadata.

Treat these as externally visible release actions.

## Upstream submission gate

Do not submit upstream until implementation is complete, tests pass, generated artifacts are consistent, the branch is synchronized with upstream, a logically independent adversarial review has completed, the final diff has been inspected, and the human user explicitly approves submission.

If approval is absent, leave the finished work in `alexsosn/cuc` and stop there.
