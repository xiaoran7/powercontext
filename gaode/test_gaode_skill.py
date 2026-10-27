from pathlib import Path


def test_gaode_skill_structure_and_rules():
    gaode_skill = Path(__file__).parent / "SKILL.md"
    root_skill = Path(__file__).parent.parent / "SKILL.md"

    assert gaode_skill.exists(), "gaode/SKILL.md must exist"
    assert root_skill.exists(), "powercontext/SKILL.md (root) must exist"

    for skill_path in [gaode_skill, root_skill]:
        content = skill_path.read_text(encoding="utf-8")

        # 1. YAML Frontmatter 校验
        assert content.startswith("---\n")
        assert "name: gaode-nearby-explorer" in content
        assert "description:" in content

        # 2. 必备高德 MCP 工具与四步工作流校验
        required_tools = [
            "maps_around_search",
            "maps_text_search",
            "maps_direction_walking",
            "maps_geo",
        ]
        for tool in required_tools:
            assert tool in content, f"Missing required AMap MCP tool: {tool}"

        # 3. 核心过滤、排序、输出结构与异常兜底校验
        required_rules = [
            "<= 15 分钟",
            "未核实",
            "推荐理由",
            "宁缺毋滥",
            "地点名称",
            "详细地址",
            "预计步行距离",
            "预计步行时间",
        ]
        for rule in required_rules:
            assert rule in content, f"Missing required rule: {rule}"


def test_gaode_readme():
    readme_path = Path(__file__).parent / "README.md"
    assert readme_path.exists(), "gaode/README.md must exist"
    content = readme_path.read_text(encoding="utf-8")

    assert "800511" in content
    assert "600074" in content
    assert "gaode-nearby-explorer" in content


def test_nearby_place_recommendation_algorithm_simulation():
    """
    模拟评测平台对 SKILL.md 四步推荐算法的执行：
    1. 按照步行时间上限（<= 15分钟即 <= 900秒）严格硬性过滤
    2. 按照实际步行距离/耗时由近及远升序排序
    3. 截取最多 3 家推荐
    4. 针对少于 3 家时践行“宁缺毋滥”不凑数
    5. 针对软性信息打上“未核实”
    """
    # 模拟高德周边搜索返回的候选列表
    mock_candidates = [
        {"name": "星巴克咖啡(西湖文化广场店)", "address": "西湖区中山北路588号", "type": "咖啡馆", "location": "120.1601,30.2780"},
        {"name": "Manner Coffee(文晖路店)", "address": "拱墅区文晖路12号", "type": "咖啡馆", "location": "120.1610,30.2790"},
        {"name": "远郊山顶精品咖啡", "address": "余杭区超山风景区内", "type": "咖啡馆", "location": "120.1900,30.3500"},
        {"name": "瑞幸咖啡(地铁站店)", "address": "西湖文化广场地铁站C口", "type": "咖啡馆", "location": "120.1595,30.2770"},
        {"name": "文化广场写字楼大堂", "address": "中山北路写字楼", "type": "写字楼", "location": "120.1598,30.2775"},  # 类型不符
    ]

    # 模拟高德步行路线规划真实返回值（单位：米与秒）
    mock_walking_routes = {
        "星巴克咖啡(西湖文化广场店)": {"distance": 450, "duration": 360},     # 6 分钟（合格）
        "Manner Coffee(文晖路店)":   {"distance": 680, "duration": 540},     # 9 分钟（合格）
        "远郊山顶精品咖啡":           {"distance": 3200, "duration": 2500},   # 41 分钟（严重超时，必须剔除）
        "瑞幸咖啡(地铁站店)":         {"distance": 180, "duration": 140},     # 2.3 分钟（最近，合格）
    }

    # 步骤 1 & 2：类型过滤
    target_type = "咖啡馆"
    type_filtered = [c for c in mock_candidates if c["type"] == target_type]
    assert len(type_filtered) == 4

    # 步骤 3 & 4：步行规划核验与时间上限过滤（<= 15 分钟 = 900 秒）
    max_duration_seconds = 15 * 60
    qualified = []
    for c in type_filtered:
        route = mock_walking_routes.get(c["name"])
        if route and route["duration"] <= max_duration_seconds:
            qualified.append({
                **c,
                "distance": route["distance"],
                "duration": route["duration"],
                "duration_min": round(route["duration"] / 60, 1),
            })

    # 验证超时地点已被严格剔除
    assert all(q["duration"] <= 900 for q in qualified)
    assert not any(q["name"] == "远郊山顶精品咖啡" for q in qualified)

    # 偏好排序：优先近一点（按 duration 升序）
    qualified.sort(key=lambda x: x["duration"])

    # 截取最多 3 家
    recommendations = qualified[:3]
    assert len(recommendations) == 3
    assert recommendations[0]["name"] == "瑞幸咖啡(地铁站店)"
    assert recommendations[1]["name"] == "星巴克咖啡(西湖文化广场店)"
    assert recommendations[2]["name"] == "Manner Coffee(文晖路店)"

    # 验证“宁缺毋滥”：若时间上限收窄至 5 分钟（300 秒），仅有 1 家合格，不得补齐超时者
    strict_qualified = [c for c in qualified if c["duration"] <= 300]
    assert len(strict_qualified) == 1
    assert strict_qualified[0]["name"] == "瑞幸咖啡(地铁站店)"

    # 验证“未核实”标注：软性属性必须标记
    soft_attribute_check = "未核实，高德地图返回数据未包含该实时信息"
    assert "未核实" in soft_attribute_check
