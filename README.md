# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                          |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_acli.py    |      118 |        0 |       30 |        1 |     99% |   79-\>81 |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_storage.py |      291 |        1 |      150 |        5 |     99% |245, 252-\>exit, 258-\>exit, 262-\>exit, 312-\>exit |
| src/heinzel/heads/api/\_\_init\_\_.py                         |       81 |        0 |       22 |        1 |     99% | 163-\>166 |
| src/heinzel/heads/cli/\_\_init\_\_.py                         |      204 |        5 |       42 |        5 |     96% |133-\>143, 153-156, 161-\>exit, 217-\>209, 272, 353-\>355 |
| src/heinzel/heads/cli/\_command.py                            |       78 |        2 |       20 |        2 |     96% |   86, 150 |
| src/heinzel/heads/cli/\_join.py                               |      141 |        0 |       32 |        1 |     99% | 211-\>216 |
| src/heinzel/heads/cli/\_serve.py                              |       59 |        4 |        8 |        1 |     93% |44-46, 54-56, 72-\>exit |
| src/heinzel/heads/cli/\_setup.py                              |      312 |        4 |       92 |        4 |     98% |270, 337, 422, 474-\>483, 497 |
| src/heinzel/heads/mcp/\_\_init\_\_.py                         |       32 |        4 |        4 |        0 |     83% |     63-67 |
| src/heinzel/heads/web/\_\_init\_\_.py                         |       83 |        7 |       20 |        3 |     90% |110-112, 147, 152-154, 161-\>exit, 163-\>exit |
| src/heinzel/kernel/\_discovery.py                             |       66 |        2 |       16 |        0 |     98% |     60-61 |
| src/heinzel/kernel/\_duration.py                              |       52 |        1 |       18 |        1 |     97% |        76 |
| src/heinzel/kernel/\_installation.py                          |       84 |        0 |       20 |        1 |     99% |123-\>exit |
| src/heinzel/kernel/\_org.py                                   |       19 |        1 |        0 |        0 |     95% |        55 |
| src/heinzel/modules/org/\_sync.py                             |      180 |        0 |       54 |        1 |     99% |143-\>exit |
| src/heinzel/modules/system/\_\_init\_\_.py                    |       30 |        7 |        2 |        0 |     72% |     58-68 |
| src/heinzel/modules/system/\_render.py                        |       50 |        0 |       20 |        1 |     99% |   35-\>37 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py              |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py                 |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py          |       73 |        3 |       18 |        2 |     95% |106, 111-112 |
| src/heinzel/modules/system/config/\_\_init\_\_.py             |       28 |        2 |        0 |        0 |     93% |    41, 58 |
| src/heinzel/modules/system/config/\_render.py                 |       18 |        1 |        8 |        1 |     92% |        18 |
| src/heinzel/modules/system/daemon/\_schedule.py               |       65 |        3 |       20 |        1 |     95% |43-44, 102 |
| src/heinzel/modules/system/directory/\_guess.py               |       82 |        0 |       24 |        1 |     99% |  98-\>104 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py             |       26 |        2 |        0 |        0 |     92% |    36, 64 |
| src/heinzel/modules/system/plugin/\_service.py                |      125 |        5 |       28 |        2 |     95% |46-\>48, 125-126, 132, 139, 232 |
| src/heinzel/modules/system/runner/\_\_init\_\_.py             |       44 |        1 |       12 |        1 |     96% |        84 |
| src/heinzel/modules/team/\_\_init\_\_.py                      |       41 |        1 |        4 |        0 |     98% |       112 |
| src/heinzel/modules/team/\_models.py                          |      218 |        1 |       36 |        0 |     99% |       260 |
| src/heinzel/modules/team/\_render.py                          |       61 |        0 |       28 |        6 |     93% |18-\>20, 30-\>32, 32-\>34, 34-\>36, 37-\>39, 54-\>56 |
| src/heinzel/modules/team/\_repo.py                            |      136 |        2 |       40 |        1 |     98% |240-\>exit, 245-246 |
| src/heinzel/modules/team/\_service.py                         |      575 |        4 |      150 |        2 |     99% |336-\>338, 874-876, 928 |
| src/heinzel/modules/team/\_yaml.py                            |      103 |        2 |       36 |        0 |     99% |     81-82 |
| src/heinzel/modules/workflow/\_\_init\_\_.py                  |       84 |        2 |        8 |        1 |     97% |   130-131 |
| src/heinzel/modules/workflow/\_contracts.py                   |      342 |        2 |      132 |        1 |     99% |   464-465 |
| src/heinzel/modules/workflow/\_models.py                      |      394 |        2 |       74 |        2 |     99% |88, 108, 514-\>512 |
| src/heinzel/modules/workflow/\_render.py                      |      213 |       15 |      104 |       10 |     90% |98, 116, 159-\>161, 164, 165-\>167, 197-203, 229, 263, 284-294, 352 |
| src/heinzel/modules/workflow/\_retention.py                   |      102 |        0 |       26 |        3 |     98% |84-\>78, 149-\>150, 149-\>exit |
| src/heinzel/modules/workflow/\_service.py                     |      697 |       14 |      230 |        4 |     98% |335, 423-424, 529-530, 921-922, 938-940, 1167-1168, 1200-1201 |
| **TOTAL**                                                     | **13406** |  **103** | **3450** |   **66** | **99%** |           |

114 files skipped due to complete coverage.


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