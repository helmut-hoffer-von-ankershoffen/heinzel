# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                          |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_acli.py    |      118 |        0 |       30 |        1 |     99% |   79-\>81 |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_storage.py |      291 |        1 |      150 |        5 |     99% |245, 252-\>exit, 258-\>exit, 262-\>exit, 312-\>exit |
| plugins/heinzel-github/src/heinzel\_github/\_client.py        |      114 |        2 |       32 |        1 |     98% |   104-105 |
| src/heinzel/heads/\_stream.py                                 |       56 |        6 |        4 |        0 |     87% | 42-46, 55 |
| src/heinzel/heads/cli/\_\_init\_\_.py                         |      239 |        4 |       58 |        6 |     97% |137-\>145, 142-\>144, 145-\>155, 166-169, 174-\>exit, 230-\>222, 411-\>413 |
| src/heinzel/heads/cli/\_chat.py                               |      142 |       13 |       38 |        5 |     90% |28-29, 70-71, 82, 106, 127-130, 152-\>exit, 164-\>exit, 183-185 |
| src/heinzel/heads/cli/\_command.py                            |       80 |        2 |       20 |        2 |     96% |   89, 153 |
| src/heinzel/heads/cli/\_join.py                               |      141 |        0 |       32 |        1 |     99% | 211-\>216 |
| src/heinzel/heads/cli/\_serve.py                              |       91 |        5 |       26 |        4 |     92% |72-\>74, 82-84, 100-\>exit, 172, 194-195 |
| src/heinzel/heads/cli/\_setup.py                              |      314 |        4 |       92 |        4 |     98% |274, 341, 426, 478-\>487, 501 |
| src/heinzel/heads/mcp/\_\_init\_\_.py                         |       79 |        2 |       16 |        2 |     96% |  138, 168 |
| src/heinzel/heads/web/\_\_init\_\_.py                         |      212 |       15 |       46 |        3 |     93% |319-321, 365, 375-\>379, 386-388, 396-\>exit, 416-417, 420-425 |
| src/heinzel/heads/web/\_devices.py                            |      100 |        2 |       12 |        0 |     98% |   125-126 |
| src/heinzel/heads/web/\_shutdown.py                           |       83 |        3 |       14 |        1 |     96% |112, 141-142 |
| src/heinzel/heads/web/\_stream.py                             |       51 |        3 |       16 |        3 |     91% |48, 85-\>84, 95-96 |
| src/heinzel/heads/web/\_terminal.py                           |      204 |       12 |       36 |        8 |     92% |35-\>40, 80, 91-\>95, 137-138, 156-157, 159, 207, 235-\>228, 241-\>exit, 249-250, 312-313, 315 |
| src/heinzel/kernel/\_bindings.py                              |      324 |        6 |       74 |        1 |     98% |144-146, 530-532 |
| src/heinzel/kernel/\_discovery.py                             |       66 |        2 |       16 |        0 |     98% |     60-61 |
| src/heinzel/kernel/\_duration.py                              |       52 |        1 |       18 |        1 |     97% |        76 |
| src/heinzel/kernel/\_installation.py                          |      154 |        2 |       40 |        0 |     99% |     85-86 |
| src/heinzel/kernel/\_org.py                                   |       31 |        1 |        0 |        0 |     97% |        55 |
| src/heinzel/kernel/\_prompter.py                              |      182 |        1 |       56 |        0 |     99% |        66 |
| src/heinzel/kernel/\_telemetry.py                             |      129 |        0 |       34 |        1 |     99% | 160-\>162 |
| src/heinzel/kernel/\_themes.py                                |      514 |        3 |      146 |        3 |     99% |318, 378, 898 |
| src/heinzel/modules/ai/access/\_\_init\_\_.py                 |       57 |       23 |       14 |        0 |     48% |46-66, 71-82 |
| src/heinzel/modules/ai/access/\_gateway.py                    |      119 |        9 |       12 |        1 |     92% |68-71, 153-154, 179, 183-184 |
| src/heinzel/modules/ai/access/\_pydantic.py                   |       99 |       13 |       18 |        1 |     86% |48-59, 78-81, 143 |
| src/heinzel/modules/ai/access/\_service.py                    |      150 |       23 |       42 |       10 |     83% |82-84, 91-93, 137, 163-164, 166-167, 217, 219, 223, 226-227, 239-240, 244, 255-256, 264-265 |
| src/heinzel/modules/graph/\_build.py                          |      268 |        2 |      122 |        3 |     99% |168, 432, 434-\>427 |
| src/heinzel/modules/graph/\_render.py                         |       28 |       23 |        8 |        0 |     14% |     15-37 |
| src/heinzel/modules/graph/\_schedule.py                       |       50 |        1 |       14 |        3 |     94% |39-\>45, 66-\>62, 76 |
| src/heinzel/modules/heinzel/\_\_init\_\_.py                   |       86 |        2 |        8 |        1 |     97% |   156-157 |
| src/heinzel/modules/heinzel/\_contracts.py                    |      342 |        2 |      132 |        1 |     99% |   464-465 |
| src/heinzel/modules/heinzel/\_models.py                       |      403 |        2 |       74 |        2 |     99% |88, 108, 515-\>513 |
| src/heinzel/modules/heinzel/\_render.py                       |      216 |       16 |      104 |       11 |     90% |42, 103, 121, 164-\>166, 169, 170-\>172, 202-208, 234, 268, 289-299, 357 |
| src/heinzel/modules/heinzel/\_retention.py                    |      102 |        0 |       26 |        3 |     98% |84-\>78, 149-\>150, 149-\>exit |
| src/heinzel/modules/heinzel/\_service.py                      |      755 |       18 |      246 |        7 |     98% |343, 431-432, 537-538, 578, 592-\>exit, 603-604, 1031-1032, 1048-1050, 1277-1278, 1310-1311, 1506 |
| src/heinzel/modules/org/\_sync.py                             |      180 |        0 |       54 |        1 |     99% |143-\>exit |
| src/heinzel/modules/system/\_render.py                        |       52 |        0 |       20 |        1 |     99% |   35-\>37 |
| src/heinzel/modules/system/\_update.py                        |      163 |        8 |       46 |        3 |     95% |93, 104-106, 173-\>185, 183-\>185, 274-275, 295-296 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py              |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py                 |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py          |       73 |        3 |       18 |        2 |     95% |106, 111-112 |
| src/heinzel/modules/system/config/\_render.py                 |       18 |        1 |        8 |        1 |     92% |        18 |
| src/heinzel/modules/system/connector/\_render.py              |       46 |       36 |       22 |        0 |     15% |20-35, 39-44, 49-55, 60-67 |
| src/heinzel/modules/system/connector/\_service.py             |       89 |        1 |       22 |        2 |     97% |82-\>81, 126 |
| src/heinzel/modules/system/daemon/\_render.py                 |       47 |        1 |       22 |        1 |     97% |        30 |
| src/heinzel/modules/system/daemon/\_schedule.py               |       65 |        3 |       20 |        1 |     95% |43-44, 102 |
| src/heinzel/modules/system/daemon/\_service.py                |      346 |        1 |      106 |        0 |     99% |        99 |
| src/heinzel/modules/system/directory/\_guess.py               |       82 |        0 |       24 |        1 |     99% |  98-\>104 |
| src/heinzel/modules/system/notify/\_\_init\_\_.py             |       34 |        2 |        0 |        0 |     94% |  102, 109 |
| src/heinzel/modules/system/notify/\_channels.py               |       48 |        5 |       26 |        3 |     86% |45, 52, 68-70 |
| src/heinzel/modules/system/notify/\_macos.py                  |      124 |        7 |       26 |        2 |     94% |73-74, 80-83, 141, 162-\>164 |
| src/heinzel/modules/system/notify/\_native.py                 |       99 |        7 |       26 |        1 |     94% |71-74, 130-131, 133 |
| src/heinzel/modules/system/notify/\_policy.py                 |       83 |        5 |       26 |        2 |     94% |57-\>59, 62-64, 90-91 |
| src/heinzel/modules/system/notify/\_render.py                 |       46 |       14 |       22 |        5 |     66% |14-18, 25, 31, 50-54, 67, 71 |
| src/heinzel/modules/system/notify/\_service.py                |      245 |       18 |       70 |        9 |     91% |177-179, 254-\>253, 280-281, 294, 328-\>336, 331-333, 339, 341, 371-\>374, 402, 436, 451-455 |
| src/heinzel/modules/system/notify/\_store.py                  |      101 |        9 |        0 |        0 |     91% |50-52, 76-77, 91-92, 106-107 |
| src/heinzel/modules/system/notify/\_watch.py                  |       34 |        7 |       10 |        4 |     75% |21-29, 40, 43-44, 59, 68 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py             |       48 |        3 |        4 |        0 |     94% |84, 102, 127 |
| src/heinzel/modules/system/plugin/\_render.py                 |       83 |       12 |       30 |        7 |     80% |38, 47, 84, 86, 88-\>87, 104-106, 116-120 |
| src/heinzel/modules/system/plugin/\_service.py                |      363 |        6 |      100 |        7 |     97% |118-\>120, 170-\>169, 405-406, 447-448, 461-\>466, 641-\>640, 658-\>657, 757-758 |
| src/heinzel/modules/system/plugin/\_sources.py                |      272 |        2 |       90 |        1 |     99% |253-\>255, 427-428 |
| src/heinzel/modules/system/runner/\_\_init\_\_.py             |       62 |        1 |       14 |        1 |     97% |       122 |
| src/heinzel/modules/system/runner/\_claude.py                 |      204 |        0 |       72 |        1 |     99% |204-\>exit |
| src/heinzel/modules/system/runner/\_codex.py                  |      180 |        1 |       60 |        5 |     98% |84-\>81, 95-\>90, 124-\>114, 129-\>114, 208 |
| src/heinzel/modules/system/runner/\_command.py                |      146 |        2 |       40 |        0 |     99% |   199-200 |
| src/heinzel/modules/system/runner/\_process.py                |       88 |        4 |        8 |        0 |     96% |   135-138 |
| src/heinzel/modules/system/runner/chat/\_service.py           |      174 |        8 |       44 |        2 |     95% |155, 158-159, 198-201, 300 |
| src/heinzel/modules/system/runner/chat/\_store.py             |      186 |        1 |       44 |        4 |     98% |263, 270-\>266, 272-\>266, 307-\>309 |
| src/heinzel/modules/system/theme/\_\_init\_\_.py              |       49 |        1 |       14 |        4 |     92% |55-\>57, 59, 60-\>62, 74-\>76 |
| src/heinzel/modules/system/theme/\_service.py                 |      208 |        3 |       56 |        2 |     98% |90, 325-326 |
| src/heinzel/modules/team/\_\_init\_\_.py                      |       41 |        1 |        4 |        0 |     98% |       112 |
| src/heinzel/modules/team/\_models.py                          |      218 |        1 |       36 |        0 |     99% |       260 |
| src/heinzel/modules/team/\_render.py                          |       61 |        0 |       28 |        6 |     93% |18-\>20, 30-\>32, 32-\>34, 34-\>36, 37-\>39, 54-\>56 |
| src/heinzel/modules/team/\_repo.py                            |      136 |        2 |       40 |        1 |     98% |240-\>exit, 245-246 |
| src/heinzel/modules/team/\_service.py                         |      575 |        4 |      150 |        2 |     99% |336-\>338, 874-876, 928 |
| src/heinzel/modules/team/\_yaml.py                            |      103 |        2 |       36 |        0 |     99% |     81-82 |
| src/heinzel/modules/telemetry/\_\_init\_\_.py                 |       44 |        2 |        0 |        0 |     95% |  110, 164 |
| src/heinzel/modules/telemetry/\_render.py                     |       15 |       10 |        2 |        0 |     29% |11-17, 22-27 |
| src/heinzel/modules/telemetry/\_service.py                    |      236 |        8 |       70 |        3 |     96% |146-\>152, 154-\>152, 162, 219-220, 289-291, 344-345 |
| **TOTAL**                                                     | **20228** |  **429** | **5074** |  **181** | **97%** |           |

131 files skipped due to complete coverage.


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