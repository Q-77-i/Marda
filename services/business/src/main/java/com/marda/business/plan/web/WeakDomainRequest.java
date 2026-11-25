package com.marda.business.plan.web;

import com.marda.business.plan.domain.WeakDomain;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record WeakDomainRequest(
        @NotBlank @Pattern(regexp = "^[a-z][a-z0-9-]*$") String domain,
        @DecimalMin("0.0") @DecimalMax("5.0") double score,
        @Size(max = 200) String advice) {

    public WeakDomain toDomain() {
        return new WeakDomain(domain, score, advice);
    }
}
