package com.marda.business.plan.application;

import com.marda.business.plan.domain.LearningTask;

import java.time.LocalDate;
import java.util.List;

/** 今日任务视图：日期按用户时区（J3 默认 Asia/Shanghai）。 */
public record TodayView(LocalDate date, List<LearningTask> tasks) {
}
