package com.marda.business.plan.application;

import java.util.List;

/** 分页结果（契约 `{items,total,page,page_size}`）。 */
public record PageResult<T>(List<T> items, long total, int page, int pageSize) {
}
