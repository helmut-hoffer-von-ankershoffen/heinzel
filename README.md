# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                      |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|---------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| packages/heinzel-clock/src/heinzel\_clock/\_\_init\_\_.py |       29 |        1 |        4 |        1 |     94% |        66 |
| src/heinzel/\_\_main\_\_.py                               |        1 |        1 |        0 |        0 |      0% |         3 |
| src/heinzel/heads/api/\_\_init\_\_.py                     |       38 |        2 |        6 |        1 |     93% |69-\>72, 79-81 |
| src/heinzel/heads/cli/\_\_init\_\_.py                     |       97 |        7 |       18 |        2 |     92% |101-\>103, 108-114, 136-\>132 |
| src/heinzel/heads/cli/\_command.py                        |       70 |        2 |       16 |        2 |     95% |   77, 120 |
| src/heinzel/heads/cli/\_serve.py                          |       18 |        4 |        0 |        0 |     78% |36-38, 46-48 |
| src/heinzel/heads/mcp/\_\_init\_\_.py                     |       32 |        4 |        4 |        0 |     83% |     63-67 |
| src/heinzel/kernel/\_boot.py                              |        9 |        4 |        0 |        0 |     56% |     11-14 |
| src/heinzel/kernel/\_config.py                            |       86 |        0 |       20 |        1 |     99% | 152-\>154 |
| src/heinzel/kernel/\_discovery.py                         |       45 |        2 |       12 |        0 |     96% |     56-57 |
| src/heinzel/kernel/\_installation.py                      |       83 |        0 |       20 |        1 |     99% |120-\>exit |
| src/heinzel/modules/system/\_\_init\_\_.py                |       25 |        7 |        2 |        0 |     67% |     42-52 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py          |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py             |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py      |       21 |        3 |        4 |        2 |     80% | 35, 40-41 |
| src/heinzel/modules/system/config/\_\_init\_\_.py         |       27 |        2 |        0 |        0 |     93% |    40, 57 |
| src/heinzel/modules/system/config/\_render.py             |       17 |        1 |        8 |        1 |     92% |        17 |
| src/heinzel/modules/system/config/\_service.py            |      105 |        4 |       22 |        0 |     97% |134, 161-163 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py         |       26 |        2 |        0 |        0 |     92% |    36, 64 |
| src/heinzel/modules/system/plugin/\_service.py            |      130 |        5 |       30 |        2 |     96% |45-\>47, 122-123, 129, 136, 231 |
| **TOTAL**                                                 | **1563** |   **54** |  **306** |   **14** | **96%** |           |

23 files skipped due to complete coverage.


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fhelmut-hoffer-von-ankershoffen%2Fheinzel%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.