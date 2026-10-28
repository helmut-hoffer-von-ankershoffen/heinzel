# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                          |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|-------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_acli.py    |      118 |        0 |       30 |        1 |     99% |   79-\>81 |
| plugins/heinzel-atlassian/src/heinzel\_atlassian/\_storage.py |      291 |        1 |      150 |        5 |     99% |245, 252-\>exit, 258-\>exit, 262-\>exit, 312-\>exit |
| plugins/heinzel-github/src/heinzel\_github/\_client.py        |      114 |        2 |       32 |        1 |     98% |   104-105 |
| plugins/heinzel-google/src/heinzel\_google/\_mail.py          |      422 |        0 |      144 |        1 |     99% | 485-\>487 |
| plugins/heinzel-tailscale/src/heinzel\_tailscale/\_cli.py     |      113 |        6 |       30 |        4 |     93% |105-\>107, 116-\>115, 135-137, 148-\>150, 152-153, 163 |
| plugins/heinzel-tailscale/src/heinzel\_tailscale/\_lock.py    |       33 |        0 |        6 |        1 |     97% | 45-\>exit |
| plugins/heinzel-tailscale/src/heinzel\_tailscale/\_service.py |      164 |        4 |       48 |        2 |     97% |44-45, 213-214 |
| src/heinzel/heads/\_stream.py                                 |       64 |        1 |        4 |        1 |     97% |47-\>49, 58 |
| src/heinzel/heads/cli/\_\_init\_\_.py                         |      248 |        6 |       64 |        6 |     96% |139-\>141, 163-166, 169, 173-\>exit, 229-\>221, 415, 428-\>430 |
| src/heinzel/heads/cli/\_chat.py                               |      147 |       13 |       40 |        5 |     90% |29-30, 71-72, 85, 109, 130-133, 157-\>exit, 169-\>exit, 188-190 |
| src/heinzel/heads/cli/\_command.py                            |      111 |        3 |       32 |        3 |     96% |89, 97, 189 |
| src/heinzel/heads/cli/\_join.py                               |      143 |        0 |       32 |        1 |     99% | 213-\>218 |
| src/heinzel/heads/cli/\_serve.py                              |      101 |        4 |       30 |        5 |     93% |83-\>85, 116-\>exit, 192, 214-215, 239 |
| src/heinzel/heads/cli/\_setup.py                              |      319 |        6 |       96 |        6 |     97% |275, 342, 427, 434, 481-\>490, 504, 507 |
| src/heinzel/heads/mcp/\_\_init\_\_.py                         |       91 |        2 |       22 |        2 |     96% |  158, 193 |
| src/heinzel/heads/web/\_\_init\_\_.py                         |      253 |       15 |       58 |        3 |     94% |412-414, 460, 470-\>474, 481-483, 491-\>exit, 511-512, 515-520 |
| src/heinzel/heads/web/\_devices.py                            |      100 |        2 |       12 |        0 |     98% |   125-126 |
| src/heinzel/heads/web/\_lifecycle.py                          |      133 |        7 |       38 |        2 |     95% |83, 99-100, 108-\>103, 110-111, 182-183 |
| src/heinzel/heads/web/\_shutdown.py                           |       83 |        3 |       14 |        1 |     96% |112, 141-142 |
| src/heinzel/heads/web/\_stream.py                             |       51 |        2 |       16 |        2 |     94% |85-\>84, 95-96 |
| src/heinzel/heads/web/\_terminal.py                           |      269 |       17 |       58 |       12 |     91% |36-\>41, 108, 119-\>123, 198-199, 208-210, 211-\>213, 219-\>exit, 235-236, 238, 267-\>exit, 317, 345-\>338, 351-\>exit, 357-360, 422-423, 425 |
| src/heinzel/heads/web/\_updates.py                            |       90 |        3 |       24 |        3 |     95% |59-\>exit, 74-75, 86 |
| src/heinzel/kernel/\_ai.py                                    |       69 |        1 |        6 |        1 |     97% |        95 |
| src/heinzel/kernel/\_bindings.py                              |      326 |        6 |       74 |        1 |     98% |171-173, 559-561 |
| src/heinzel/kernel/\_contributions.py                         |      353 |        5 |       72 |        4 |     98% |170, 208, 508, 642-643 |
| src/heinzel/kernel/\_discovery.py                             |       66 |        2 |       16 |        0 |     98% |     60-61 |
| src/heinzel/kernel/\_duration.py                              |       52 |        1 |       18 |        1 |     97% |        76 |
| src/heinzel/kernel/\_git.py                                   |      122 |        0 |       34 |        1 |     99% |227-\>exit |
| src/heinzel/kernel/\_identity.py                              |      122 |        4 |       10 |        1 |     96% |44-45, 199-200 |
| src/heinzel/kernel/\_installation.py                          |      154 |        2 |       40 |        0 |     99% |     85-86 |
| src/heinzel/kernel/\_local\_web.py                            |       23 |        1 |        4 |        1 |     93% |        39 |
| src/heinzel/kernel/\_org.py                                   |       31 |        1 |        0 |        0 |     97% |        55 |
| src/heinzel/kernel/\_prompter.py                              |      182 |        1 |       56 |        0 |     99% |        66 |
| src/heinzel/kernel/\_session\_io.py                           |      103 |        2 |       18 |        1 |     98% |     83-84 |
| src/heinzel/kernel/\_session\_store.py                        |      240 |        5 |       52 |        3 |     97% |243-245, 266-267, 277-\>283 |
| src/heinzel/kernel/\_settings.py                              |       66 |        2 |       16 |        2 |     95% |  196, 198 |
| src/heinzel/kernel/\_skills.py                                |      380 |       11 |      120 |        2 |     97% |164-166, 375-376, 477-481, 610, 615, 631 |
| src/heinzel/kernel/\_soundscapes.py                           |      271 |        3 |       70 |        0 |     99% |   105-107 |
| src/heinzel/kernel/\_telemetry.py                             |      129 |        0 |       34 |        1 |     99% | 160-\>162 |
| src/heinzel/kernel/\_themes.py                                |      562 |        3 |      162 |        3 |     99% |318, 378, 983 |
| src/heinzel/modules/ai/\_\_init\_\_.py                        |       49 |       25 |       14 |        0 |     38% |38, 43-62, 82-94 |
| src/heinzel/modules/ai/\_models.py                            |       32 |        5 |        2 |        1 |     82% |46-48, 51-52 |
| src/heinzel/modules/ai/\_usage.py                             |      123 |        7 |       16 |        0 |     95% |107-108, 136-138, 144-145 |
| src/heinzel/modules/ai/access/\_\_init\_\_.py                 |       66 |       24 |       14 |        0 |     52% |52-72, 77-88, 143 |
| src/heinzel/modules/ai/access/\_chatgpt.py                    |      172 |       10 |       26 |        2 |     94% |64-65, 81, 139-\>143, 147-149, 226, 275-278 |
| src/heinzel/modules/ai/access/\_discovery.py                  |      117 |        6 |       26 |        2 |     94% |108-109, 157, 166-168 |
| src/heinzel/modules/ai/access/\_gateway.py                    |      121 |        9 |       12 |        1 |     92% |68-71, 158-159, 184, 188-189 |
| src/heinzel/modules/ai/access/\_pydantic.py                   |      159 |       12 |       38 |        4 |     91% |68-\>73, 75-86, 126-127, 229, 234 |
| src/heinzel/modules/ai/access/\_service.py                    |      152 |       23 |       42 |       10 |     83% |84-86, 93-95, 145, 171-172, 174-175, 225, 227, 231, 234-235, 247-248, 252, 274-275, 283-284 |
| src/heinzel/modules/graph/\_build.py                          |      268 |        2 |      122 |        3 |     99% |168, 432, 434-\>427 |
| src/heinzel/modules/graph/\_render.py                         |       28 |       23 |        8 |        0 |     14% |     15-37 |
| src/heinzel/modules/graph/\_schedule.py                       |       50 |        1 |       14 |        3 |     94% |39-\>45, 66-\>62, 76 |
| src/heinzel/modules/heinzel/\_\_init\_\_.py                   |       88 |        2 |        8 |        1 |     97% |   158-159 |
| src/heinzel/modules/heinzel/\_contracts.py                    |      342 |        2 |      132 |        1 |     99% |   464-465 |
| src/heinzel/modules/heinzel/\_engine.py                       |      227 |        2 |       42 |        2 |     99% |406, 408-\>410, 451 |
| src/heinzel/modules/heinzel/\_models.py                       |      406 |        2 |       74 |        2 |     99% |88, 108, 548-\>546 |
| src/heinzel/modules/heinzel/\_render.py                       |      216 |       16 |      104 |       11 |     90% |42, 103, 121, 164-\>166, 169, 170-\>172, 202-208, 234, 268, 289-299, 357 |
| src/heinzel/modules/heinzel/\_retention.py                    |      102 |        0 |       24 |        1 |     99% |   84-\>78 |
| src/heinzel/modules/heinzel/\_service.py                      |      759 |       18 |      248 |        7 |     98% |345, 433-434, 539-540, 580, 594-\>exit, 605-606, 1037-1038, 1054-1056, 1283-1284, 1316-1317, 1513 |
| src/heinzel/modules/heinzel/\_session\_bodies.py              |       21 |        4 |        2 |        1 |     78% |29-30, 37-38 |
| src/heinzel/modules/heinzel/\_session\_crud.py                |      182 |       25 |       48 |        9 |     85% |105-106, 111-113, 115-116, 122-124, 129-130, 189-190, 217, 221-222, 247-248, 271-272, 276-277, 311-312 |
| src/heinzel/modules/heinzel/\_session\_repository.py          |       76 |        2 |        8 |        1 |     96% |     27-28 |
| src/heinzel/modules/org/\_sync.py                             |      180 |        0 |       54 |        1 |     99% |143-\>exit |
| src/heinzel/modules/system/\_render.py                        |       53 |        0 |       20 |        1 |     99% |   35-\>37 |
| src/heinzel/modules/system/\_update.py                        |      281 |        8 |       84 |        4 |     97% |153, 164-166, 239-\>251, 249-\>251, 370-\>397, 531-532, 563-564 |
| src/heinzel/modules/system/agent/\_\_init\_\_.py              |       16 |        2 |        0 |        0 |     88% |    15, 27 |
| src/heinzel/modules/system/agent/\_service.py                 |       62 |        1 |       26 |        1 |     98% |        36 |
| src/heinzel/modules/system/changelog/\_\_init\_\_.py          |       74 |        3 |       18 |        2 |     95% |109, 114-115 |
| src/heinzel/modules/system/claude/\_render.py                 |       35 |        4 |       14 |        3 |     86% |17-\>29, 29-\>39, 40, 50-52 |
| src/heinzel/modules/system/claude/\_scan.py                   |      328 |       33 |       98 |       15 |     88% |69-70, 74-75, 170-\>169, 188, 248-251, 259, 284, 299, 305-306, 313-\>307, 320-321, 326-\>334, 329-330, 343-\>348, 346-347, 367-368, 370, 410-411, 420-421, 423, 450-\>448, 453, 461-462, 477, 480 |
| src/heinzel/modules/system/config/\_\_init\_\_.py             |       34 |        1 |        0 |        0 |     97% |        90 |
| src/heinzel/modules/system/config/\_render.py                 |       18 |        1 |        8 |        1 |     92% |        18 |
| src/heinzel/modules/system/config/\_service.py                |      287 |        2 |       80 |        2 |     99% |234-235, 415-\>exit |
| src/heinzel/modules/system/connector/\_service.py             |       89 |        0 |       22 |        1 |     99% |   82-\>81 |
| src/heinzel/modules/system/daemon/\_\_init\_\_.py             |       47 |        1 |        0 |        0 |     98% |        62 |
| src/heinzel/modules/system/daemon/\_render.py                 |       47 |        1 |       22 |        1 |     97% |        30 |
| src/heinzel/modules/system/daemon/\_schedule.py               |       65 |        3 |       20 |        1 |     95% |43-44, 102 |
| src/heinzel/modules/system/daemon/\_service.py                |      357 |        1 |      110 |        0 |     99% |       112 |
| src/heinzel/modules/system/daemon/\_status\_item.py           |       40 |        1 |       14 |        1 |     96% |        30 |
| src/heinzel/modules/system/directory/\_guess.py               |       82 |        0 |       24 |        1 |     99% |  98-\>104 |
| src/heinzel/modules/system/lifecycle/\_authority.py           |       70 |        4 |       30 |        3 |     93% |98-\>exit, 104-105, 111-112 |
| src/heinzel/modules/system/lifecycle/\_store.py               |      369 |       17 |      116 |       10 |     94% |48, 177-\>exit, 191-192, 195-196, 221, 229, 237, 239, 242, 419, 424-427, 449, 455-456, 458-\>442, 543 |
| src/heinzel/modules/system/notify/\_\_init\_\_.py             |       34 |        2 |        0 |        0 |     94% |  102, 109 |
| src/heinzel/modules/system/notify/\_channels.py               |       48 |        5 |       26 |        3 |     86% |45, 52, 68-70 |
| src/heinzel/modules/system/notify/\_macos.py                  |      140 |        7 |       32 |        2 |     95% |77-78, 84-87, 168, 189-\>191 |
| src/heinzel/modules/system/notify/\_native.py                 |      101 |        7 |       26 |        1 |     94% |72-75, 131-132, 134 |
| src/heinzel/modules/system/notify/\_policy.py                 |       83 |        5 |       26 |        2 |     94% |57-\>59, 62-64, 90-91 |
| src/heinzel/modules/system/notify/\_render.py                 |       57 |       11 |       32 |        8 |     74% |14-18, 25, 31, 52, 54, 64-\>67, 81, 85 |
| src/heinzel/modules/system/notify/\_service.py                |      251 |       17 |       74 |        8 |     92% |177-179, 254-\>253, 280-281, 294, 328-\>341, 333-335, 348, 385-\>388, 416, 450, 465-469 |
| src/heinzel/modules/system/notify/\_store.py                  |      101 |        9 |        0 |        0 |     91% |50-52, 76-77, 91-92, 106-107 |
| src/heinzel/modules/system/notify/\_watch.py                  |       34 |        7 |       10 |        4 |     75% |21-29, 40, 43-44, 59, 68 |
| src/heinzel/modules/system/plugin/\_\_init\_\_.py             |       57 |        3 |        6 |        0 |     95% |118, 136, 161 |
| src/heinzel/modules/system/plugin/\_receipts.py               |      155 |       25 |       18 |        4 |     82% |92-93, 101-106, 131-132, 138-140, 142-143, 157-159, 180, 185-187, 213-215 |
| src/heinzel/modules/system/plugin/\_render.py                 |       85 |       12 |       30 |        7 |     80% |44, 53, 90, 92, 94-\>93, 111-113, 123-127 |
| src/heinzel/modules/system/plugin/\_service.py                |      526 |       27 |      160 |       17 |     93% |130-\>132, 190, 203-\>202, 359-360, 369-370, 376, 423-425, 445, 449, 504-505, 546-547, 560-\>565, 775-\>781, 856-\>855, 873-\>872, 955-956, 960-961, 974, 977-978, 993, 1022-1023, 1038-1039 |
| src/heinzel/modules/system/plugin/\_sources.py                |      272 |        2 |       90 |        1 |     99% |253-\>255, 427-428 |
| src/heinzel/modules/system/plugin/docs/\_build.py             |       24 |        0 |        8 |        1 |     97% |   22-\>24 |
| src/heinzel/modules/system/plugin/docs/\_files.py             |      104 |        6 |       32 |        2 |     94% |51-52, 122-123, 126-127 |
| src/heinzel/modules/system/plugin/docs/\_render.py            |      245 |       23 |      112 |       18 |     87% |48-\>52, 60-\>67, 81, 85, 88-\>91, 96, 110-\>112, 132, 179-\>191, 197, 227-\>229, 236, 259-\>266, 297, 316, 321-325, 327, 331-\>318, 411-432 |
| src/heinzel/modules/system/plugin/docs/\_service.py           |      287 |       25 |       98 |        5 |     89% |156, 389-395, 436, 457, 476-479, 563-565, 614, 673-674, 688-692 |
| src/heinzel/modules/system/runner/\_\_init\_\_.py             |       64 |        1 |       14 |        1 |     97% |       125 |
| src/heinzel/modules/system/runner/\_claude.py                 |      229 |        0 |       82 |        1 |     99% |240-\>exit |
| src/heinzel/modules/system/runner/\_codex.py                  |      180 |        1 |       60 |        5 |     98% |84-\>81, 95-\>90, 124-\>114, 129-\>114, 208 |
| src/heinzel/modules/system/runner/\_command.py                |      149 |        2 |       42 |        0 |     99% |   215-216 |
| src/heinzel/modules/system/runner/\_process.py                |       88 |        4 |        8 |        0 |     96% |   135-138 |
| src/heinzel/modules/system/runner/chat/\_service.py           |      198 |        9 |       54 |        3 |     95% |160, 163-164, 218-221, 247, 350 |
| src/heinzel/modules/system/runner/chat/\_store.py             |      186 |        1 |       44 |        4 |     98% |264, 271-\>267, 273-\>267, 308-\>310 |
| src/heinzel/modules/system/skill/\_\_init\_\_.py              |       70 |        3 |       18 |        6 |     90% |55, 58-\>60, 68, 71-\>73, 83, 84-\>86 |
| src/heinzel/modules/system/skill/\_export.py                  |      158 |        3 |       52 |        2 |     98% |168-170, 219-\>208, 232-\>234 |
| src/heinzel/modules/system/skill/\_service.py                 |      149 |        2 |       48 |        0 |     99% |   139-140 |
| src/heinzel/modules/system/sound/\_\_init\_\_.py              |       41 |        1 |       12 |        2 |     94% |66, 67-\>69 |
| src/heinzel/modules/system/state/\_service.py                 |      215 |        8 |       40 |        1 |     96% |235-\>237, 258-261, 299-300, 381-382 |
| src/heinzel/modules/system/state/\_sink.py                    |      240 |       24 |       74 |       10 |     89% |154-156, 158-159, 185-186, 188-189, 262-263, 319-320, 322-323, 327-328, 333-334, 336-337, 338-\>331, 358-360 |
| src/heinzel/modules/system/state/\_streams.py                 |      151 |       15 |       38 |        5 |     89% |76-78, 125-\>123, 134-136, 142, 176-178, 183, 188, 195, 209-210 |
| src/heinzel/modules/system/theme/\_\_init\_\_.py              |       50 |        1 |       14 |        4 |     92% |56-\>58, 60, 61-\>63, 75-\>77 |
| src/heinzel/modules/system/theme/\_service.py                 |      212 |        3 |       58 |        2 |     98% |91, 338-339 |
| src/heinzel/modules/system/ui/\_\_init\_\_.py                 |      112 |       33 |       40 |        6 |     64% |58-64, 69, 81, 86, 88, 90, 99-103, 113-119, 157, 180-188 |
| src/heinzel/modules/system/ui/\_service.py                    |      299 |       13 |       96 |        4 |     96% |105-106, 117-\>116, 260-261, 350-351, 377-378, 400-401, 447-448, 498 |
| src/heinzel/modules/team/\_\_init\_\_.py                      |       41 |        1 |        4 |        0 |     98% |       112 |
| src/heinzel/modules/team/\_models.py                          |      219 |        1 |       36 |        0 |     99% |       268 |
| src/heinzel/modules/team/\_render.py                          |       61 |        0 |       28 |        6 |     93% |18-\>20, 30-\>32, 32-\>34, 34-\>36, 37-\>39, 54-\>56 |
| src/heinzel/modules/team/\_service.py                         |      575 |        4 |      150 |        2 |     99% |336-\>338, 874-876, 928 |
| src/heinzel/modules/team/\_yaml.py                            |      103 |        2 |       36 |        0 |     99% |     81-82 |
| src/heinzel/modules/telemetry/\_\_init\_\_.py                 |       44 |        2 |        0 |        0 |     95% |  110, 164 |
| src/heinzel/modules/telemetry/\_render.py                     |       15 |       10 |        2 |        0 |     29% |11-17, 22-27 |
| src/heinzel/modules/telemetry/\_service.py                    |      236 |        8 |       70 |        3 |     96% |146-\>152, 154-\>152, 162, 219-220, 289-291, 344-345 |
| **TOTAL**                                                     | **29841** |  **795** | **7414** |  **349** | **97%** |           |

165 files skipped due to complete coverage.


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