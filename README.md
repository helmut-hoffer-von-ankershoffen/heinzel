# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                 |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|----------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/heinzel/\_\_main\_\_.py                          |        1 |        1 |        0 |        0 |      0% |         3 |
| src/heinzel/heads/api/\_\_init\_\_.py                |       71 |        0 |       18 |        1 |     99% | 130-\>133 |
| src/heinzel/heads/cli/\_\_init\_\_.py                |      160 |        4 |       30 |        4 |     96% |116-\>118, 125-128, 133-\>exit, 166-\>160, 276-\>278 |
| src/heinzel/heads/cli/\_command.py                   |       76 |        2 |       20 |        2 |     96% |   85, 146 |
| src/heinzel/heads/cli/\_serve.py                     |       39 |        4 |        6 |        1 |     89% |43-45, 53-55, 71-\>exit |
| src/heinzel/heads/mcp/\_\_init\_\_.py                |       32 |        4 |        4 |        0 |     83% |     63-67 |
| src/heinzel/kernel/\_discovery.py                    |       66 |        2 |       16 |        0 |     98% |     60-61 |
| src/heinzel/kernel/\_installation.py                 |       84 |        0 |       20 |        1 |     99% |123-\>exit |
| src/heinzel/modules/system/\_\_init\_\_.py           |       30 |        7 |        2 |        0 |     72% |     56-66 |
| src/heinzel/modules/system/\_render.py               |       50 |        0 |       20 |        1 |     99% |   35-\>37 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py     |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py        |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py |       21 |        3 |        4 |        2 |     80% | 35, 40-41 |
| src/heinzel/modules/system/config/\_\_init\_\_.py    |       28 |        2 |        0 |        0 |     93% |    41, 58 |
| src/heinzel/modules/system/config/\_render.py        |       18 |        1 |        8 |        1 |     92% |        18 |
| src/heinzel/modules/system/config/\_service.py       |      148 |        2 |       40 |        1 |     98% |   163-164 |
| src/heinzel/modules/system/daemon/\_schedule.py      |       65 |        3 |       20 |        1 |     95% |43-44, 102 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py    |       26 |        2 |        0 |        0 |     92% |    36, 64 |
| src/heinzel/modules/system/plugin/\_service.py       |      124 |        5 |       28 |        2 |     95% |38-\>40, 115-116, 122, 129, 222 |
| src/heinzel/modules/workflow/\_contracts.py          |      340 |        2 |      132 |        1 |     99% |   462-463 |
| src/heinzel/modules/workflow/\_models.py             |      317 |        2 |       62 |        2 |     99% |74, 94, 427-\>425 |
| src/heinzel/modules/workflow/\_render.py             |      121 |        8 |       50 |        3 |     91% |94, 110, 181-191 |
| src/heinzel/modules/workflow/\_service.py            |      446 |        5 |      134 |        2 |     99% |283, 350-353, 441-442 |
| **TOTAL**                                            | **6166** |   **62** | **1422** |   **26** | **99%** |           |

60 files skipped due to complete coverage.


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