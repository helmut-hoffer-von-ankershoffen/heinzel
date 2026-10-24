# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                          |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_acli.py    |      118 |        0 |       30 |        1 |     99% |   79-\>81 |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_storage.py |      291 |        1 |      150 |        5 |     99% |245, 252-\>exit, 258-\>exit, 262-\>exit, 312-\>exit |
| plugins/heinzel-github/src/heinzel\_github/\_client.py        |      114 |        2 |       32 |        1 |     98% |   104-105 |
| src/heinzel/heads/\_stream.py                                 |       56 |        6 |        4 |        0 |     87% | 42-46, 55 |
| src/heinzel/heads/cli/\_\_init\_\_.py                         |      211 |        5 |       46 |        7 |     95% |137-\>145, 142-\>144, 145-\>155, 165-168, 173-\>exit, 229-\>221, 284, 365-\>367 |
| src/heinzel/heads/cli/\_chat.py                               |      142 |       13 |       38 |        5 |     90% |28-29, 70-71, 82, 106, 127-130, 152-\>exit, 164-\>exit, 183-185 |
| src/heinzel/heads/cli/\_command.py                            |       78 |        2 |       20 |        2 |     96% |   86, 150 |
| src/heinzel/heads/cli/\_join.py                               |      141 |        0 |       32 |        1 |     99% | 211-\>216 |
| src/heinzel/heads/cli/\_serve.py                              |       84 |        4 |       24 |        3 |     94% |72-\>74, 82-84, 100-\>exit, 185-186 |
| src/heinzel/heads/cli/\_setup.py                              |      312 |        4 |       92 |        4 |     98% |270, 337, 422, 474-\>483, 497 |
| src/heinzel/heads/mcp/\_\_init\_\_.py                         |       71 |        2 |       12 |        2 |     95% |  122, 152 |
| src/heinzel/heads/web/\_\_init\_\_.py                         |      207 |       15 |       46 |        3 |     93% |309-311, 353, 363-\>367, 371-373, 381-\>exit, 401-402, 405-410 |
| src/heinzel/heads/web/\_devices.py                            |      100 |        2 |       12 |        0 |     98% |   125-126 |
| src/heinzel/heads/web/\_stream.py                             |       46 |        5 |       14 |        4 |     82% |36, 66-\>65, 70-\>73, 75-78 |
| src/heinzel/heads/web/\_terminal.py                           |      190 |       14 |       36 |        8 |     90% |33-\>38, 74, 85-\>89, 119-120, 136-137, 139, 178, 206-\>199, 212-\>exit, 218-221, 283-284, 286 |
| src/heinzel/kernel/\_bindings.py                              |      289 |        3 |       66 |        0 |     99% |   137-139 |
| src/heinzel/kernel/\_discovery.py                             |       66 |        2 |       16 |        0 |     98% |     60-61 |
| src/heinzel/kernel/\_duration.py                              |       52 |        1 |       18 |        1 |     97% |        76 |
| src/heinzel/kernel/\_org.py                                   |       31 |        1 |        0 |        0 |     97% |        55 |
| src/heinzel/kernel/\_progress.py                              |       14 |        1 |        0 |        0 |     93% |        41 |
| src/heinzel/kernel/\_telemetry.py                             |      129 |        0 |       34 |        1 |     99% | 160-\>162 |
| src/heinzel/kernel/\_themes.py                                |      377 |        2 |      118 |        2 |     99% |  270, 643 |
| src/heinzel/modules/graph/\_build.py                          |      268 |        2 |      122 |        3 |     99% |168, 432, 434-\>427 |
| src/heinzel/modules/graph/\_render.py                         |       28 |       23 |        8 |        0 |     14% |     15-37 |
| src/heinzel/modules/graph/\_schedule.py                       |       50 |        1 |       14 |        3 |     94% |39-\>45, 66-\>62, 76 |
| src/heinzel/modules/org/\_sync.py                             |      180 |        0 |       54 |        1 |     99% |143-\>exit |
| src/heinzel/modules/system/\_\_init\_\_.py                    |       30 |        7 |        2 |        0 |     72% |     58-68 |
| src/heinzel/modules/system/\_render.py                        |       50 |        0 |       20 |        1 |     99% |   35-\>37 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py              |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py                 |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py          |       73 |        3 |       18 |        2 |     95% |106, 111-112 |
| src/heinzel/modules/system/config/\_\_init\_\_.py             |       31 |        1 |        0 |        0 |     97% |        64 |
| src/heinzel/modules/system/config/\_render.py                 |       18 |        1 |        8 |        1 |     92% |        18 |
| src/heinzel/modules/system/connector/\_render.py              |       46 |       36 |       22 |        0 |     15% |20-35, 39-44, 49-55, 60-67 |
| src/heinzel/modules/system/connector/\_service.py             |       89 |        1 |       22 |        2 |     97% |82-\>81, 126 |
| src/heinzel/modules/system/daemon/\_schedule.py               |       65 |        3 |       20 |        1 |     95% |43-44, 102 |
| src/heinzel/modules/system/directory/\_guess.py               |       82 |        0 |       24 |        1 |     99% |  98-\>104 |
| src/heinzel/modules/system/notify/\_\_init\_\_.py             |       34 |        2 |        0 |        0 |     94% |  102, 109 |
| src/heinzel/modules/system/notify/\_channels.py               |       48 |        5 |       26 |        3 |     86% |45, 52, 68-70 |
| src/heinzel/modules/system/notify/\_macos.py                  |      124 |        7 |       26 |        2 |     94% |67-68, 74-77, 135, 156-\>158 |
| src/heinzel/modules/system/notify/\_native.py                 |       99 |        7 |       26 |        1 |     94% |71-74, 130-131, 133 |
| src/heinzel/modules/system/notify/\_policy.py                 |       83 |        5 |       26 |        2 |     94% |57-\>59, 62-64, 90-91 |
| src/heinzel/modules/system/notify/\_render.py                 |       46 |       14 |       22 |        5 |     66% |14-18, 25, 31, 50-54, 67, 71 |
| src/heinzel/modules/system/notify/\_service.py                |      245 |       18 |       70 |        9 |     91% |177-179, 254-\>253, 280-281, 294, 328-\>336, 331-333, 339, 341, 371-\>374, 402, 436, 451-455 |
| src/heinzel/modules/system/notify/\_store.py                  |      101 |        9 |        0 |        0 |     91% |50-52, 76-77, 91-92, 106-107 |
| src/heinzel/modules/system/notify/\_watch.py                  |       34 |        7 |       10 |        4 |     75% |21-29, 40, 43-44, 59, 68 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py             |       40 |        3 |        0 |        0 |     92% |74, 92, 117 |
| src/heinzel/modules/system/plugin/\_render.py                 |       63 |        4 |       20 |        6 |     88% |38, 47, 80, 82, 84-\>83, 93-\>95 |
| src/heinzel/modules/system/plugin/\_service.py                |      361 |        6 |      100 |        7 |     97% |117-\>119, 169-\>168, 397-398, 439-440, 453-\>458, 633-\>632, 650-\>649, 749-750 |
| src/heinzel/modules/system/plugin/\_sources.py                |      272 |        2 |       90 |        1 |     99% |253-\>255, 427-428 |
| src/heinzel/modules/system/runner/\_\_init\_\_.py             |       49 |        1 |       14 |        1 |     97% |       102 |
| src/heinzel/modules/system/runner/\_claude.py                 |      204 |        0 |       72 |        2 |     99% |204-\>exit, 208-\>exit |
| src/heinzel/modules/system/runner/\_command.py                |      146 |        2 |       40 |        0 |     99% |   199-200 |
| src/heinzel/modules/system/runner/\_process.py                |       88 |        4 |        8 |        0 |     96% |   135-138 |
| src/heinzel/modules/system/runner/chat/\_service.py           |      174 |        8 |       44 |        2 |     95% |155, 158-159, 198-201, 300 |
| src/heinzel/modules/system/runner/chat/\_store.py             |      186 |        1 |       44 |        4 |     98% |263, 270-\>266, 272-\>266, 307-\>309 |
| src/heinzel/modules/system/theme/\_\_init\_\_.py              |       40 |        1 |       12 |        3 |     92% |54-\>56, 58, 59-\>61 |
| src/heinzel/modules/system/theme/\_service.py                 |      125 |        1 |       32 |        1 |     99% |        59 |
| src/heinzel/modules/team/\_\_init\_\_.py                      |       41 |        1 |        4 |        0 |     98% |       112 |
| src/heinzel/modules/team/\_models.py                          |      218 |        1 |       36 |        0 |     99% |       260 |
| src/heinzel/modules/team/\_render.py                          |       61 |        0 |       28 |        6 |     93% |18-\>20, 30-\>32, 32-\>34, 34-\>36, 37-\>39, 54-\>56 |
| src/heinzel/modules/team/\_repo.py                            |      136 |        2 |       40 |        1 |     98% |240-\>exit, 245-246 |
| src/heinzel/modules/team/\_service.py                         |      575 |        4 |      150 |        2 |     99% |336-\>338, 874-876, 928 |
| src/heinzel/modules/team/\_yaml.py                            |      103 |        2 |       36 |        0 |     99% |     81-82 |
| src/heinzel/modules/telemetry/\_\_init\_\_.py                 |       44 |        2 |        0 |        0 |     95% |  110, 164 |
| src/heinzel/modules/telemetry/\_render.py                     |       15 |       10 |        2 |        0 |     29% |11-17, 22-27 |
| src/heinzel/modules/telemetry/\_service.py                    |      236 |        8 |       70 |        3 |     96% |146-\>152, 154-\>152, 162, 219-220, 289-291, 344-345 |
| src/heinzel/modules/workflow/\_\_init\_\_.py                  |       86 |        2 |        8 |        1 |     97% |   132-133 |
| src/heinzel/modules/workflow/\_contracts.py                   |      342 |        2 |      132 |        1 |     99% |   464-465 |
| src/heinzel/modules/workflow/\_models.py                      |      394 |        2 |       74 |        2 |     99% |88, 108, 515-\>513 |
| src/heinzel/modules/workflow/\_render.py                      |      213 |       15 |      104 |       10 |     90% |98, 116, 159-\>161, 164, 165-\>167, 197-203, 229, 263, 284-294, 352 |
| src/heinzel/modules/workflow/\_retention.py                   |      102 |        0 |       26 |        3 |     98% |84-\>78, 149-\>150, 149-\>exit |
| src/heinzel/modules/workflow/\_service.py                     |      718 |       17 |      236 |        6 |     98% |338, 426-427, 532-533, 573, 587-\>exit, 598-599, 960-961, 977-979, 1206-1207, 1239-1240 |
| **TOTAL**                                                     | **18571** |  **341** | **4698** |  **154** | **98%** |           |

127 files skipped due to complete coverage.


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