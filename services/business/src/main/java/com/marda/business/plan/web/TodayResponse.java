package com.marda.business.plan.web;

import com.marda.business.plan.application.TodayView;

import java.time.LocalDate;
import java.util.List;

public record TodayResponse(LocalDate date, List<LearningTaskResponse> tasks) {

    public static TodayResponse from(TodayView view) {
        return new TodayResponse(
                view.date(),
                view.tasks().stream().map(LearningTaskResponse::from).toList());
    }
}
