package com.marda.business.common;

import com.marda.business.plan.application.PlanConflictException;
import com.marda.business.plan.application.PlanNotFoundException;
import com.marda.business.plan.domain.InvalidPlanInputException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** 统一错误信封；业务文案中文，code 稳定供前端判断。 */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiError> handleApi(ApiException exception, HttpServletRequest request) {
        return error(exception.status(), exception.code(), exception.getMessage(),
                exception.details(), request);
    }

    @ExceptionHandler(BadRequestException.class)
    public ResponseEntity<ApiError> handleBadRequest(BadRequestException exception,
                                                     HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST", exception.getMessage(), Map.of(), request);
    }

    @ExceptionHandler(InvalidPlanInputException.class)
    public ResponseEntity<ApiError> handleInvalidPlan(InvalidPlanInputException exception,
                                                      HttpServletRequest request) {
        return error(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR",
                exception.getMessage(), Map.of(), request);
    }

    @ExceptionHandler(PlanNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(PlanNotFoundException exception,
                                                   HttpServletRequest request) {
        return error(HttpStatus.NOT_FOUND, "NOT_FOUND", exception.getMessage(), Map.of(), request);
    }

    @ExceptionHandler(PlanConflictException.class)
    public ResponseEntity<ApiError> handleConflict(PlanConflictException exception,
                                                   HttpServletRequest request) {
        return error(HttpStatus.CONFLICT, exception.code(), exception.getMessage(),
                exception.details(), request);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleBodyValidation(MethodArgumentNotValidException exception,
                                                         HttpServletRequest request) {
        Map<String, Object> details = new LinkedHashMap<>();
        for (FieldError fieldError : exception.getBindingResult().getFieldErrors()) {
            details.putIfAbsent(fieldError.getField(), fieldError.getDefaultMessage());
        }
        return error(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR",
                "请求参数校验失败", details, request);
    }

    @ExceptionHandler(HandlerMethodValidationException.class)
    public ResponseEntity<ApiError> handleMethodValidation(HandlerMethodValidationException exception,
                                                           HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST", "查询参数校验失败", Map.of(), request);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ApiError> handleConstraint(ConstraintViolationException exception,
                                                     HttpServletRequest request) {
        Map<String, Object> details = new LinkedHashMap<>();
        exception.getConstraintViolations()
                .forEach(violation -> details.put(violation.getPropertyPath().toString(),
                        violation.getMessage()));
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST", "查询参数校验失败", details, request);
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ApiError> handleMissingHeader(MissingRequestHeaderException exception,
                                                        HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST",
                "缺少请求头：" + exception.getHeaderName(), Map.of(), request);
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiError> handleTypeMismatch(MethodArgumentTypeMismatchException exception,
                                                       HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST",
                "参数格式错误：" + exception.getName(), Map.of(), request);
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(HttpMessageNotReadableException exception,
                                                     HttpServletRequest request) {
        return error(HttpStatus.BAD_REQUEST, "BAD_REQUEST", "请求体格式错误", Map.of(), request);
    }

    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ApiError> handleIntegrity(DataIntegrityViolationException exception,
                                                    HttpServletRequest request) {
        log.warn("数据约束冲突 request_id={}", requestId(request));
        return error(HttpStatus.CONFLICT, "CONFLICT", "数据冲突", Map.of(), request);
    }

    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<ApiError> handleDataAccess(DataAccessException exception,
                                                     HttpServletRequest request) {
        log.error("数据库不可用 request_id={}", requestId(request), exception);
        return error(HttpStatus.SERVICE_UNAVAILABLE, "DEPENDENCY_UNAVAILABLE",
                "数据库暂不可用", Map.of(), request);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleUnexpected(Exception exception, HttpServletRequest request) {
        log.error("未处理异常 request_id={}", requestId(request), exception);
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "服务内部错误",
                Map.of(), request);
    }

    private static ResponseEntity<ApiError> error(HttpStatus status, String code, String message,
                                                  Map<String, Object> details,
                                                  HttpServletRequest request) {
        return ResponseEntity.status(status)
                .body(new ApiError(code, message, requestId(request),
                        details == null ? Map.of() : details));
    }

    private static String requestId(HttpServletRequest request) {
        Object value = request.getAttribute(RequestIdFilter.ATTRIBUTE);
        return value == null ? UUID.randomUUID().toString() : value.toString();
    }
}
