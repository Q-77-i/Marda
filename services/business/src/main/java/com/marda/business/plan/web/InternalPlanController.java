package com.marda.business.plan.web;

import com.marda.business.common.BadRequestException;
import com.marda.business.plan.application.CreatePlanResult;
import com.marda.business.plan.application.LearningPlanService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 内部 API：只允许内网 + service token；J5 的事件消费者 / Agent 读取用。 */
@RestController
@RequestMapping("/internal/v1")
@Validated
public class InternalPlanController {

    private final LearningPlanService plans;

    public InternalPlanController(LearningPlanService plans) {
        this.plans = plans;
    }

    @PostMapping("/learning-plans/from-report")
    public ResponseEntity<LearningPlanResponse> fromReport(
            @RequestHeader("X-User-Id") String headerUserId,
            @Valid @RequestBody FromReportRequest request) {
        if (!headerUserId.equals(request.userId())) {
            throw new BadRequestException("X-User-Id 与事件 payload.user_id 不一致");
        }
        CreatePlanResult result = plans.create(request.toPlanInput(), request.interviewId(), true);
        return switch (result.outcome()) {
            case CREATED -> ResponseEntity.status(HttpStatus.CREATED)
                    .body(LearningPlanResponse.from(result.view()));
            case EXISTING -> ResponseEntity.ok(LearningPlanResponse.from(result.view()));
            case SKIPPED -> ResponseEntity.<LearningPlanResponse>status(HttpStatus.NO_CONTENT).build();
        };
    }

    @GetMapping("/learning-plans/{userId}/next")
    public NextPlanResponse next(
            @PathVariable String userId,
            @RequestParam(defaultValue = "3") @Min(1) @Max(10) int limit) {
        return NextPlanResponse.from(plans.next(userId, limit));
    }
}
