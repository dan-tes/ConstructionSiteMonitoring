# Ручная разметка новых роликов (data/yt) по контактным листам contact_yt/*.jpg.
# id: (project_days, start_s, end_s, [(start_s, phase), ...]) ; None — исключён (не стройка /
# интерьер / монтаж разных камер без хронологии). Длительности: из названия/описания,
# где есть (см. hints.tsv), иначе оценка по масштабу объекта.
L = {
 "-j-hYdRpNrg": (540, 0, 280, [(0, "Site Preparation"), (140, "Foundation"), (225, "Structural Frame"), (250, "Finishing")]),
 "-r1tziNYr2E": (150, 0, 192, [(0, "Structural Frame")]),  # деревянный каркас
 "0TS4w_Wxieg": (730, 8, 133, [(8, "Earthwork"), (33, "Structural Frame"), (42, "Finishing"), (75, "External Works")]),
 "0XaqV7kY-oY": (140, 12, 82, [(12, "Foundation"), (40, "Structural Frame")]),
 "1DhsVXs8frY": (420, 0, 178, [(0, "Foundation"), (58, "Structural Frame"), (90, "Finishing"), (140, "External Works")]),
 "1HdI1xEdb8U": None,  # стеллажи внутри готового склада
 "1pCUeJ8F9Qw": (120, 3, 162, [(3, "Earthwork"), (12, "Foundation"), (42, "Masonry"), (145, "Structural Frame")]),
 "1qAgECdNdhA": None,  # cinematic, склейки ракурсов, концовка — чужие города
 "2rwnlspHIMg": (540, 0, 92, [(0, "Earthwork"), (18, "Foundation"), (33, "Structural Frame"), (50, "Finishing"), (80, "External Works")]),
 "3pan2Ej959s": (450, 3, 29, [(3, "Earthwork"), (9, "Structural Frame"), (17, "Finishing"), (24, "External Works")]),
 "3tc4BzrWjVo": None,  # панорамная склейка, не хронология
 "49VfKxYbvbs": (540, 5, 79, [(5, "Structural Frame"), (37, "Finishing")]),
 "56YCjIgYjAw": (550, 0, 160, [(0, "Site Preparation"), (22, "Earthwork"), (33, "Foundation"), (45, "Structural Frame")]),
 "5Mzk7YC5mcg": (400, 10, 165, [(10, "Structural Frame"), (78, "Finishing"), (110, "External Works")]),
 "71c_Fdpo6aY": None,  # cinematic дрон, разные точки съёмки, интерьеры
 "7Ku8JMMCw98": (85, 4, 56, [(4, "Earthwork"), (8, "Foundation"), (14, "Structural Frame"), (26, "Finishing")]),
 "7zlI9hjSIrs": (600, 0, 60, [(0, "Site Preparation"), (9, "Earthwork"), (15, "Foundation"), (36, "Structural Frame")]),
 "7zmy7ELnJt4": (730, 5, 530, [(5, "Foundation"), (40, "Structural Frame"), (140, "Masonry"), (380, "Finishing")]),
 "8EC2EN1p3nY": (1100, 8, 128, [(8, "Site Preparation"), (17, "Earthwork"), (30, "Foundation"), (43, "Structural Frame"), (100, "Finishing")]),
 "8hHNgAD7vVk": (120, 6, 101, [(6, "Masonry"), (33, "Structural Frame"), (63, "Finishing")]),  # стены из блока, стропила/кровля
 "9DY6i3xJ3CA": None,  # склейка ракурсов + интерьер, хронология неясна
 "9OF-heIVJa8": (900, 8, 135, [(8, "Structural Frame"), (95, "Finishing"), (125, "External Works")]),
 "ABvUTcVqfMY": (790, 0, 530, [(0, "Earthwork"), (90, "Foundation"), (140, "Structural Frame"), (270, "Finishing")]),
 "B1U67-a4zwQ": (730, 0, 455, [(0, "Site Preparation"), (45, "Earthwork"), (125, "Foundation"), (230, "Structural Frame"), (390, "Finishing")]),
 "BBGW9h7Q2rg": (60, 0, 192, [(0, "Foundation"), (140, "Structural Frame")]),  # фундаментные плиты, начало каркаса
 "C3iI6S7TuCA": None,  # монтаж с разных ракурсов + интерьеры
 "EEhtpqlOOWs": (730, 0, 530, [(0, "Earthwork"), (70, "Foundation"), (130, "Structural Frame"), (270, "Finishing"), (470, "External Works")]),
 "EX3kR_o34PE": (730, 14, 97, [(14, "Earthwork"), (28, "Foundation"), (50, "Structural Frame"), (78, "Finishing")]),
 "EcxFWl-sdrk": None,  # склейка разных камер и интерьеров бассейна
 "Ej7R2Ig0CyA": None,  # склейка разных камер, снос соседнего здания
 "FZ1xRwQ4vGo": (490, 0, 430, [(0, "Structural Frame"), (230, "Finishing")]),
 "HxO3yjCfyIo": (1100, 0, 78, [(0, "Foundation"), (10, "Structural Frame"), (55, "Finishing")]),
 "Jo1Kqy680P8": (540, 8, 137, [(8, "Site Preparation"), (18, "Structural Frame"), (62, "Finishing"), (110, "External Works")]),
 "KV3wcnMOs30": (730, 8, 112, [(8, "Earthwork"), (30, "Foundation"), (60, "Structural Frame")]),
 "Lb9_NrwkrWU": (1030, 6, 100, [(6, "Foundation"), (72, "Structural Frame"), (87, "Finishing")]),
 "MzRPUJaQNJE": (730, 0, 61, [(0, "Site Preparation"), (9, "Earthwork"), (14, "Foundation"), (30, "Structural Frame"), (52, "Finishing")]),
 "OEvuKTdxjWc": (700, 10, 60, [(10, "Earthwork"), (20, "Foundation"), (27, "Structural Frame"), (40, "Finishing")]),
 "Pe-3093Xwqk": (900, 7, 95, [(7, "Earthwork"), (16, "Foundation"), (27, "Structural Frame")]),
 "PwaDAh7Dgwc": (730, 0, 430, [(0, "Earthwork"), (30, "Foundation"), (60, "Structural Frame"), (220, "Finishing")]),
 "QNlfjua-Xac": None,  # склейка разных камер/зданий
 "R8GEf2XQPiU": None,  # монтаж с разных точек, инфографика
 "RTmspH9IcJ4": (600, 4, 131, [(4, "Site Preparation"), (10, "Earthwork"), (28, "Foundation"), (55, "Structural Frame"), (100, "Finishing")]),
 "S76oAFaXr2Q": None,  # камера меняет ракурс, ночь/засветки
 "U3Cm-2E4Ly8": (210, 0, 86, [(0, "Site Preparation"), (12, "Structural Frame"), (38, "Finishing"), (68, "External Works")]),
 "UKTF9fVJo24": None,  # две камеры, вторая — только готовый фасад
 "WGreiPv0y9Y": (400, 0, 185, [(0, "Site Preparation"), (40, "Earthwork")]),
 "WnvdvAvOjmY": (240, 25, 310, [(25, "Site Preparation"), (60, "Foundation")]),  # снос, затем фундамент/стены подвала
 "Ze3jksDzkq0": (730, 0, 170, [(0, "Earthwork"), (12, "Foundation"), (42, "Structural Frame"), (85, "Finishing"), (140, "External Works")]),
 "ZhpQxZnnhww": (400, 0, 47, [(0, "Earthwork"), (13, "Foundation"), (32, "Structural Frame")]),
 "aNwM1eyuv7Q": (790, 0, 470, [(0, "Earthwork"), (60, "Foundation"), (120, "Structural Frame"), (300, "Finishing")]),
 "aQU5Vo0vk1g": None,  # дрон, разные ракурсы
 "ab_2_bHt_GE": None,  # разные камеры, панорамы города
 "bxaKlMRE8QE": (730, 0, 120, [(0, "Site Preparation"), (20, "Earthwork"), (70, "Foundation")]),
 "gJZ5CkEJIAI": None,  # ручная съёмка, интерьеры
 "hI6ETnICWRE": None,  # половина кадров — ночь/ИК, фаза почти не меняется
 "iyM5K1_k_18": (1430, 0, 360, [(0, "Site Preparation"), (40, "Earthwork"), (95, "Foundation"), (130, "Structural Frame"), (200, "Finishing")]),
 "j5k6xO90hKU": None,  # cinematic, разные ракурсы
 "k2b9IsVilvM": (900, 20, 230, [(20, "Earthwork"), (60, "Foundation"), (80, "Structural Frame"), (190, "Finishing")]),
 "knaul_G0-gg": None,  # cinematic дрон, разные ракурсы
 "lTtpGAQZpts": None,  # дрон/разные ракурсы, хронология есть, но кадр скачет
 "lvq9K7IiNgk": None,  # cinematic, разные камеры
 "mAe_ht6fyno": None,  # дрон + интерьеры
 "mdRvUgwQ__I": None,  # небоскрёб издалека + инфографика
 "q6wfV2Sx2RY": None,  # дрон, разные ракурсы
 "qbu8tAhc65I": (120, 20, 262, [(20, "Site Preparation"), (40, "Foundation"), (75, "Structural Frame"), (140, "External Works")]),  # 4 месяца (титр)
 "qhpXJ5TLo1U": None,  # интервью
 "te9VdEGYDng": (700, 3, 150, [(3, "Site Preparation"), (22, "Earthwork"), (40, "Foundation"), (70, "Structural Frame"), (118, "Finishing")]),
 "usJUxinwsYA": None,  # реконструкция, интерьеры, ночь
 "uyDydwTBsVw": (300, 5, 285, [(5, "Site Preparation"), (30, "Earthwork"), (70, "Foundation"), (100, "Structural Frame"), (190, "Finishing"), (225, "External Works")]),
 "xbeAoglYkns": (970, 0, 212, [(0, "Structural Frame"), (125, "Finishing")]),  # 32 месяца
 "zkA5PM4NUfM": (730, 7, 110, [(7, "Earthwork"), (28, "Foundation"), (44, "Structural Frame"), (62, "Finishing")]),
}
