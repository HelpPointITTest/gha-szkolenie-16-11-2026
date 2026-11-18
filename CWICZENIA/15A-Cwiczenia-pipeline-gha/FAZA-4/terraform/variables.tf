variable "environment" {
  description = "Środowisko DigitalOcean. Test pozostaje na self-hosted runnerze."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Dozwolone środowiska DigitalOcean: dev oraz prod."
  }
}

variable "student_name" {
  description = "Imię i nazwisko uczestnika w formacie jan-kowalski, bez polskich znaków."
  type        = string

  validation {
    condition     = length(var.student_name) >= 3 && length(var.student_name) <= 40 && can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)+$", var.student_name))
    error_message = "Podaj 3–40 znaków w formacie jan-kowalski: małe litery ASCII, cyfry i pojedyncze myślniki pomiędzy członami."
  }
}

variable "region" {
  description = "Slug regionu DigitalOcean dostępnego na koncie."
  type        = string
  default     = "fra1"
}

variable "droplet_size" {
  description = "Rozmiar Dropleta x64 dostępny w wybranym regionie."
  type        = string
  default     = "s-1vcpu-1gb"
}

variable "ssh_allowed_cidr" {
  description = "Publiczny adres IPv4 self-hosted runnera z maską /32."
  type        = string

  validation {
    condition     = can(cidrnetmask(var.ssh_allowed_cidr)) && try(tonumber(split("/", var.ssh_allowed_cidr)[1]) == 32, false)
    error_message = "Podaj pojedynczy publiczny IPv4 runnera z maską /32, np. 203.0.113.10/32."
  }
}
