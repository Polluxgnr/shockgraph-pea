Closes #

## What
<!-- One or two sentences. -->

## Why
<!-- Which grading criterion or user need this serves. -->

## How to test
```bash
make test-db && make lint && make test
```
<!-- Plus the curl / Swagger call that shows it working. -->

## Choices to defend
<!-- The decision a reviewer or the teacher could question, and why you made it (alternatives rejected). -->

## Checklist
- [ ] Only files of my role (the ownership hook passed)
- [ ] `make lint` >= 9.5 and `make test` green locally
- [ ] Cypher parameterised, `LIMIT` on every list, variable-length paths bounded
- [ ] `docs/defense/<my letter>.md` updated
