package com.marda.business.plan.web;

import com.marda.business.common.ApiException;
import com.marda.business.common.BadRequestException;
import com.marda.business.plan.application.CreatePlanResult;
import com.marda.business.plan.application.LearningPlanService;
import com.marda.business.plan.domain.PlanStatus;
import com.marda.business.plan.domain.SkipReason;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

/** 学习计划公开 API：浏览器 → 网关（JWT / 限流）→ 本服务。 */
@RestController
@RequestMapping("/api/v1/learning")
@Validated
public class PlanController {

    private final LearningPlanService plans;

    public PlanController(LearningPlanService plans) {
        this.plans = plans;
    }

    @PostMapping("/plans")
    public ResponseEntity<LearningPlanResponse> create(
            @RequestHeader("X-User-Id") String userId,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @Valid @RequestBody CreatePlanRequest request) {
        CreatePlanResult result = plans.create(request.toPlanInput(userId), idempotencyKey, false);
        if (result.outcome() == CreatePlanResult.Outcome.SKIPPED) {
            throw skipped(result.skipReason());
        }
        HttpStatus status = result.outcome() == CreatePlanResult.Outcome.CREATED
                ? HttpStatus.CREATED
                : HttpStatus.OK;
        return ResponseEntity.status(status).body(LearningPlanResponse.from(result.view()));
    }

    @GetMapping("/plans")
    public PaginatedPlansResponse list(
            @RequestHeader("X-User-Id") String userId,
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "1") @Min(1) int page,
            @RequestParam(name = "page_size", defaultValue = "20") @Min(1) @Max(50) int pageSize) {
        return PaginatedPlansResponse.from(
                plans.list(userId, planStatus(status), page, pageSize));
    }

    @GetMapping("/plans/{planId}")
    public ResponseEntity<LearningPlanResponse> get(
            @RequestHeader("X-User-Id") String userId,
            @PathVariable UUID planId) {
        var result = plans.getCached(userId, planId);
        return ResponseEntity.ok()
                .header("X-Cache", result.outcome().name())
                .body(LearningPlanResponse.from(result.value()));
    }

    @PatchMapping("/tasks/{taskId}")
    public LearningTaskResponse updateTask(
            @RequestHeader("X-User-Id") String userId,
            @PathVariable UUID taskId,
            @Valid @RequestBody TaskUpdateRequest request) {
        return LearningTaskResponse.from(
                plans.updateTask(userId, taskId, request.toCommand()));
    }

    @GetMapping("/today")
    public ResponseEntity<TodayResponse> today(@RequestHeader("X-User-Id") String userId) {
        var result = plans.todayCached(userId);
        return ResponseEntity.ok()
                .header("X-Cache", result.outcome().name())
                .body(TodayResponse.from(result.value()));
    }

    private static PlanStatus planStatus(String value) {
        if (value == null) {
            return null;
        }
        try {
            return PlanStatus.fromApi(value);
        } catch (IllegalArgumentException exception) {
            throw new BadRequestException("未知计划状态：" + value);
        }
    }

    private static ApiException skipped(SkipReason reason) {
        return switch (reason) {
            case NO_WEAK_DOMAINS -> new ApiException(
                    HttpStatus.UNPROCESSABLE_ENTITY,
                    "NO_WEAK_DOMAINS",
                    "本场没有可生成计划的短板域",
                    Map.of());
            case UNSUPPORTED_INTERVIEW_TYPE -> new ApiException(
                    HttpStatus.UNPROCESSABLE_ENTITY,
                    "UNSUPPORTED_INTERVIEW_TYPE",
                    "行为面报告不生成学习计划",
                    Map.of());
        };
    }
}
